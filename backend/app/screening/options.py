"""Unusual options activity metrics.

Methodology (also documented in docs/METHODOLOGY.md):

* **Options volume** = total contracts traded (calls + puts, all expirations) in one regular
  session. Open interest is a *separate* field and is never used as volume.
* **Baseline** (configurable):
    - ``trailing_20d_avg`` (default): mean daily options volume of the 20 sessions that end
      21 sessions before the evaluation date, i.e. the comparable one-session average from one
      month earlier. Comparing one session to a one-session average keeps periods equivalent.
    - ``single_session_1m_ago``: volume of the single session 21 sessions earlier.
* **Volume change %** = (current / baseline − 1) × 100. A 300% increase means current ≥ 4× baseline.
* **IV Rank (52w)** = (IV_now − min IV) / (max IV − min IV) × 100 over the last 252 sessions of
  30-day constant-maturity ATM implied volatility. Needs ≥ ``min_iv_history_obs`` observations.
* **Put/Call ratio** = put volume / call volume for the evaluation session (volume, not OI).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

from app.config import OptionsCriteria
from app.screening.types import CriterionResult, Outcome, lerp_score


@dataclass
class DailyOptionsVolume:
    session: date
    call_volume: int
    put_volume: int

    @property
    def total(self) -> int:
        return self.call_volume + self.put_volume


@dataclass
class ContractActivity:
    """Per-contract day aggregate (from a chain snapshot) or an individual trade."""

    contract: str
    option_type: str  # "call" | "put"
    expiration: date
    strike: float
    volume: int
    open_interest: int | None
    price: float | None  # last/avg trade price per share
    bid: float | None = None
    ask: float | None = None
    is_single_trade: bool = False  # True when this row is one print (block), not a day aggregate

    @property
    def premium(self) -> float | None:
        return None if self.price is None else self.price * self.volume * 100


@dataclass
class VolumeBaseline:
    current: int | None
    baseline: float | None
    change_pct: float | None
    method: str
    current_session: date | None
    baseline_sessions: tuple[date, date] | None
    note: str = ""


def volume_baseline(history: Sequence[DailyOptionsVolume], method: str) -> VolumeBaseline:
    """``history`` sorted ascending; the last element is the evaluation session."""
    h = sorted(history, key=lambda r: r.session)
    if not h:
        return VolumeBaseline(None, None, None, method, None, None, "no options volume history")
    cur = h[-1]
    prior = h[:-1]
    if method == "single_session_1m_ago":
        if len(prior) < 21:
            return VolumeBaseline(cur.total, None, None, method, cur.session, None, "need 21 prior sessions")
        ref = prior[-21]
        base, span = float(ref.total), (ref.session, ref.session)
    else:  # trailing_20d_avg ending 21 sessions earlier
        if len(prior) < 40:
            return VolumeBaseline(cur.total, None, None, method, cur.session, None, "need 40 prior sessions")
        window = prior[-40:-20]
        base = sum(r.total for r in window) / len(window)
        span = (window[0].session, window[-1].session)
    change = None if base <= 0 else (cur.total / base - 1.0) * 100.0
    return VolumeBaseline(cur.total, base, change, method, cur.session, span)


def iv_rank(iv_series: Sequence[float], window: int = 252, min_obs: int = 200) -> float | None:
    """IV Rank 0..100 from a chronologically ordered IV series whose last value is 'now'."""
    s = [x for x in iv_series[-window:] if x is not None and x > 0]
    if len(s) < min_obs:
        return None
    lo, hi = min(s), max(s)
    if hi == lo:
        return None
    return (s[-1] - lo) / (hi - lo) * 100.0


def put_call_ratio(put_volume: int | None, call_volume: int | None) -> float | None:
    if put_volume is None or call_volume is None or call_volume <= 0:
        return None
    return put_volume / call_volume


def moneyness(option_type: str, strike: float, spot: float, atm_band_pct: float = 2.0) -> str:
    if spot <= 0:
        return "unknown"
    if abs(strike - spot) / spot * 100 <= atm_band_pct:
        return "ATM"
    if option_type == "call":
        return "ITM" if strike < spot else "OTM"
    return "ITM" if strike > spot else "OTM"


def trade_side(c: ContractActivity) -> str:
    """Aggressor inference from print vs NBBO. Only meaningful for single trades."""
    if not c.is_single_trade or c.price is None or c.bid is None or c.ask is None:
        return "unknown"
    if c.price >= c.ask:
        return "at/above ask (buyer-initiated)"
    if c.price <= c.bid:
        return "at/below bid (seller-initiated)"
    return "between bid/ask (indeterminate)"


def opening_inference(c: ContractActivity) -> str:
    """Volume > prior open interest implies at least part of the volume opened new positions.
    It cannot prove direction; next-day OI change confirms. Reported as an inference."""
    if c.open_interest is None:
        return "unknown"
    if c.volume > c.open_interest:
        return "likely opening (volume exceeds prior OI)"
    return "indeterminate (could be opening or closing)"


@dataclass
class OptionsSnapshot:
    ticker: str
    spot: float | None
    history: list[DailyOptionsVolume]
    iv_history: list[float]
    provider_iv_rank: float | None = None  # used only if own history is insufficient
    contracts: list[ContractActivity] = field(default_factory=list)


def evaluate_options(snap: OptionsSnapshot, cfg: OptionsCriteria) -> CriterionResult:
    vb = volume_baseline(snap.history, cfg.baseline_method)
    cur = snap.history[-1] if snap.history else None
    pcr = put_call_ratio(cur.put_volume, cur.call_volume) if cur else None

    ivr = iv_rank(snap.iv_history, cfg.iv_rank_window_days, cfg.min_iv_history_obs)
    ivr_source = "computed (252-session ATM IV30)"
    if ivr is None and snap.provider_iv_rank is not None:
        ivr, ivr_source = snap.provider_iv_rank, "provider 1y IV rank"

    largest = []
    for c in sorted(snap.contracts, key=lambda c: c.premium or 0, reverse=True)[:10]:
        largest.append({
            "contract": c.contract, "type": c.option_type, "expiration": c.expiration.isoformat(),
            "strike": c.strike, "volume": c.volume, "open_interest": c.open_interest,
            "premium": c.premium, "moneyness": moneyness(c.option_type, c.strike, snap.spot or 0, cfg.atm_band_pct),
            "is_block": bool(c.is_single_trade and (
                (c.premium or 0) >= cfg.large_trade_min_premium or c.volume >= cfg.large_trade_min_contracts)),
            "side": trade_side(c), "open_close": opening_inference(c),
        })

    call_v = cur.call_volume if cur else None
    put_v = cur.put_volume if cur else None
    concentration = None
    if call_v is not None and put_v is not None and (call_v + put_v) > 0:
        concentration = "calls" if call_v >= put_v else "puts"

    metrics = {
        "current_volume": vb.current, "baseline_volume": vb.baseline, "volume_change_pct": vb.change_pct,
        "baseline_method": vb.method, "current_session": vb.current_session.isoformat() if vb.current_session else None,
        "baseline_sessions": [d.isoformat() for d in vb.baseline_sessions] if vb.baseline_sessions else None,
        "call_volume": call_v, "put_volume": put_v, "put_call_ratio": pcr,
        "put_call_period": vb.current_session.isoformat() if vb.current_session else None,
        "iv_rank": ivr, "iv_rank_source": ivr_source, "concentration": concentration,
        "largest_contracts": largest,
    }

    missing = []
    if vb.change_pct is None:
        missing.append(f"volume baseline ({vb.note or 'baseline is zero'})")
    elif vb.baseline is not None and vb.baseline < cfg.min_baseline_volume:
        missing.append(f"baseline volume {vb.baseline:.0f} < minimum {cfg.min_baseline_volume}")
    if ivr is None:
        missing.append("IV rank (insufficient IV history)")
    if pcr is None:
        missing.append("put/call ratio (no call volume)")
    if missing:
        return CriterionResult("options", Outcome.MISSING, cfg.hard, 0.0,
                               "Insufficient options data: " + "; ".join(missing), metrics)

    checks = [
        (vb.change_pct >= cfg.min_volume_change_pct, f"volume change {vb.change_pct:.0f}% (need ≥{cfg.min_volume_change_pct:.0f}%)"),
        (ivr > cfg.min_iv_rank, f"IV rank {ivr:.1f} (need >{cfg.min_iv_rank:.0f})"),
        (pcr < cfg.max_put_call_ratio, f"put/call {pcr:.3f} (need <{cfg.max_put_call_ratio:.2f})"),
    ]
    ok = all(c[0] for c in checks)
    score = (
        0.5 * lerp_score(vb.change_pct, cfg.min_volume_change_pct * 0.5, 1500, 0, 100)
        + 0.25 * lerp_score(ivr, 0, 80)
        + 0.25 * lerp_score(pcr, 1.0, 0.0)
    )
    expl = ("Passes" if ok else "Fails") + ": " + "; ".join(("✓ " if c[0] else "✗ ") + c[1] for c in checks)
    expl += (". Activity is concentrated in " + concentration + "." if concentration else "")
    expl += " Large call volume is not automatically bullish — it may be hedging, spreads or closing trades."
    return CriterionResult("options", Outcome.PASS if ok else Outcome.FAIL, cfg.hard, score, expl, metrics)
