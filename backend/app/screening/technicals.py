"""Technical analysis and support detection on daily OHLCV bars.

Support candidates (all must be *below* the current price):
  * Swing-low zones: fractal lows (lowest low within ±``swing_window`` bars) on daily and
    weekly bars, clustered when within ``zone_tolerance_pct``. A zone is *validated* when it
    has ≥ ``min_zone_touches`` touches.
  * 50/100/200-day simple moving averages.
  * High-volume nodes from a 120-session volume-by-price profile.
  * Fibonacci retracements (38.2/50/61.8%) of the 52-week low→high range.

The **primary support** is the nearest validated swing-low zone below price; confluence with
other candidates within the zone tolerance raises its strength. Distance to support =
(price − zone top) / zone top × 100 (0 when price is inside the zone).

Breakdown: the last close is more than ``breakdown_pct`` below the nearest validated zone
that price traded above within the last 20 sessions, without a close back above it (reclaim).

Trade plan (long): entry = zone top up to current price; stop = zone low − 0.5 × ATR(14);
Target 1 = nearest resistance zone (swing highs) above price; Target 2 = the next resistance
zone, or the 52-week high if none. If no resistance is found the target is reported as
missing, never invented.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.config import TechnicalCriteria
from app.screening.types import CriterionResult, Outcome


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI."""
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0)


def macd(close: pd.Series, fast=12, slow=26, signal=9) -> pd.DataFrame:
    m = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    sig = m.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": m, "signal": sig, "hist": m - sig})


def bollinger(close: pd.Series, n=20, k=2.0) -> pd.DataFrame:
    mid = sma(close, n)
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    return pd.DataFrame({"mid": mid, "upper": mid + k * sd, "lower": mid - k * sd})


def atr(df: pd.DataFrame, n=14) -> pd.Series:
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rolling_vwap(df: pd.DataFrame, n=20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["volume"]).rolling(n).sum() / df["volume"].rolling(n).sum()


def swing_points(series: pd.Series, window: int, kind: str) -> list[tuple[pd.Timestamp, float]]:
    vals = series.to_numpy()
    idx = series.index
    pts = []
    for i in range(window, len(vals) - window):
        seg = vals[i - window:i + window + 1]
        if kind == "low" and vals[i] == seg.min():
            pts.append((idx[i], float(vals[i])))
        if kind == "high" and vals[i] == seg.max():
            pts.append((idx[i], float(vals[i])))
    return pts


@dataclass
class Zone:
    low: float
    high: float
    touches: int
    last_touch: pd.Timestamp
    sources: set[str]

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2

    def to_dict(self) -> dict:
        return {"low": round(self.low, 4), "high": round(self.high, 4), "touches": self.touches,
                "last_touch": self.last_touch.date().isoformat(), "sources": sorted(self.sources)}


def cluster_zones(points: list[tuple[pd.Timestamp, float, str]], tol_pct: float) -> list[Zone]:
    zones: list[Zone] = []
    for ts, px, src in sorted(points, key=lambda p: p[1]):
        if zones and px <= zones[-1].low * (1 + tol_pct / 100):
            z = zones[-1]
            z.high = max(z.high, px)
            z.touches += 1
            z.last_touch = max(z.last_touch, ts)
            z.sources.add(src)
        else:
            zones.append(Zone(px, px, 1, ts, {src}))
    return zones


def volume_profile(df: pd.DataFrame, sessions=120, bins=40, top=3) -> list[float]:
    d = df.tail(sessions)
    if d.empty:
        return []
    tp = ((d["high"] + d["low"] + d["close"]) / 3).to_numpy()
    hist, edges = np.histogram(tp, bins=bins, weights=d["volume"].to_numpy())
    centers = (edges[:-1] + edges[1:]) / 2
    order = np.argsort(hist)[::-1][:top]
    return [float(centers[i]) for i in order]


def classify(distance_pct: float | None, cfg: TechnicalCriteria) -> str:
    if distance_pct is None:
        return "no_support_found"
    if distance_pct <= cfg.strong_pct:
        return "strong_support"
    if distance_pct <= cfg.near_pct:
        return "near_support"
    if distance_pct <= cfg.approaching_pct:
        return "approaching_support"
    return "extended"


CLASS_SCORE = {"strong_support": 100, "near_support": 75, "approaching_support": 45, "extended": 10,
               "no_support_found": 0, "broken_support": 0}


def analyze(df: pd.DataFrame, cfg: TechnicalCriteria) -> dict:
    """``df``: columns open/high/low/close/volume, ascending DatetimeIndex (daily, split-adjusted)."""
    df = df.sort_index()
    close = df["close"]
    price = float(close.iloc[-1])
    out: dict = {"price": price, "as_of": df.index[-1].date().isoformat(), "bars": len(df)}
    smas = {n: sma(close, n) for n in (20, 50, 100, 200)}
    out["sma"] = {str(n): (None if pd.isna(s.iloc[-1]) else float(s.iloc[-1])) for n, s in smas.items()}
    r = rsi(close)
    out["rsi14"] = None if pd.isna(r.iloc[-1]) else float(r.iloc[-1])
    m = macd(close).iloc[-1]
    out["macd"] = {k: float(v) for k, v in m.items()}
    bb = bollinger(close).iloc[-1]
    out["bollinger"] = {k: (None if pd.isna(v) else float(v)) for k, v in bb.items()}
    a = atr(df)
    out["atr14"] = None if pd.isna(a.iloc[-1]) else float(a.iloc[-1])
    vw = rolling_vwap(df)
    out["vwap20"] = None if pd.isna(vw.iloc[-1]) else float(vw.iloc[-1])
    avg_vol20 = df["volume"].iloc[-21:-1].mean() if len(df) > 21 else np.nan
    out["relative_volume"] = None if not avg_vol20 else float(df["volume"].iloc[-1] / avg_vol20)
    out["avg_dollar_volume20"] = float((df["close"] * df["volume"]).tail(20).mean())

    yr = df.tail(252)
    hi52, lo52 = float(yr["high"].max()), float(yr["low"].min())
    out["high_52w"], out["low_52w"] = hi52, lo52
    fibs = {f"{int(f*1000)/10}%": hi52 - (hi52 - lo52) * f for f in (0.382, 0.5, 0.618)}
    out["fibonacci"] = fibs

    # trend structure
    s50, s200 = out["sma"]["50"], out["sma"]["200"]
    if s50 and s200:
        out["trend"] = "uptrend" if price > s50 > s200 else "downtrend" if price < s50 < s200 else "mixed"
    else:
        out["trend"] = "insufficient history"

    w = df.resample("W-FRI").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    mo = df.resample("ME").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    out["weekly_trend"] = _trend(w["close"], 10, 40)
    out["monthly_trend"] = _trend(mo["close"], 6, 12)

    lows = [(t, p, "daily_swing") for t, p in swing_points(df["low"], cfg.swing_window, "low")]
    lows += [(t, p, "weekly_swing") for t, p in swing_points(w["low"], 2, "low")]
    highs = [(t, p, "daily_swing") for t, p in swing_points(df["high"], cfg.swing_window, "high")]
    highs += [(t, p, "weekly_swing") for t, p in swing_points(w["high"], 2, "high")]
    sup_zones = cluster_zones(lows, cfg.zone_tolerance_pct)
    res_zones = cluster_zones(highs, cfg.zone_tolerance_pct)

    hvn = volume_profile(df)
    out["high_volume_nodes"] = hvn
    confluence = [(f"sma{n}", v) for n, v in out["sma"].items() if v and n != "20"]
    confluence += [("hvn", v) for v in hvn] + [(f"fib{k}", v) for k, v in fibs.items()]
    for z in sup_zones:
        for name, lvl in confluence:
            if z.low * (1 - cfg.zone_tolerance_pct / 100) <= lvl <= z.high * (1 + cfg.zone_tolerance_pct / 100):
                z.sources.add(name)

    validated = [z for z in sup_zones if z.touches >= cfg.min_zone_touches]
    below = [z for z in validated if z.low <= price]
    primary = max(below, key=lambda z: z.high) if below else None

    # breakdown: price fell > breakdown_pct under a validated zone it was above within 20 bars
    broken = None
    recent_max = float(close.tail(20).max())
    for z in sorted(validated, key=lambda z: z.low, reverse=True):
        if z.low > price and recent_max >= z.low and price < z.low * (1 - cfg.breakdown_pct / 100):
            broken = z
            break

    if primary:
        distance = 0.0 if price <= primary.high else (price - primary.high) / primary.high * 100
    else:
        distance = None
    klass = "broken_support" if broken and (primary is None or distance > cfg.approaching_pct) else classify(distance, cfg)

    # buying volume near the zone: avg volume on bars whose low touched the zone vs 50d avg
    buying_volume_ratio = None
    if primary:
        touch = df[(df["low"] <= primary.high * (1 + cfg.zone_tolerance_pct / 100)) & (df["low"] >= primary.low * (1 - cfg.zone_tolerance_pct / 100))]
        up_touch = touch[touch["close"] > touch["open"]]
        base = df["volume"].tail(50).mean()
        if len(up_touch) and base:
            buying_volume_ratio = float(up_touch["volume"].mean() / base)

    res_above = sorted([z for z in res_zones if z.low > price], key=lambda z: z.low)
    plan = None
    if primary and out["atr14"]:
        entry_low, entry_high = primary.high, price
        entry = price
        stop = primary.low - 0.5 * out["atr14"]
        t1 = res_above[0].low if res_above else None
        t2 = res_above[1].low if len(res_above) > 1 else (hi52 if hi52 > (t1 or price) * 1.001 else None)
        risk = entry - stop
        plan = {
            "direction": "long", "entry": round(entry, 4),
            "entry_range": [round(min(entry_low, entry_high), 4), round(max(entry_low, entry_high), 4)],
            "stop_loss": round(stop, 4), "target1": t1 and round(t1, 4), "target2": t2 and round(t2, 4),
            "risk_per_share": round(risk, 4),
            "rr_target1": round((t1 - entry) / risk, 2) if t1 and risk > 0 else None,
            "rr_target2": round((t2 - entry) / risk, 2) if t2 and risk > 0 else None,
            "method": "stop = zone low − 0.5×ATR14; targets = next resistance zones (swing highs)",
        }

    out.update({
        "support_zones": [z.to_dict() for z in sorted(validated, key=lambda z: z.low, reverse=True)[:8]],
        "resistance_zones": [z.to_dict() for z in res_above[:5]],
        "primary_support": primary.to_dict() if primary else None,
        "support_distance_pct": distance, "classification": klass,
        "broken_support": broken.to_dict() if broken else None,
        "buying_volume_ratio": buying_volume_ratio, "trade_plan": plan,
    })
    return out


def _trend(s: pd.Series, fast: int, slow: int) -> str:
    if len(s) < slow:
        return "insufficient history"
    f, sl, p = s.rolling(fast).mean().iloc[-1], s.rolling(slow).mean().iloc[-1], s.iloc[-1]
    return "uptrend" if p > f > sl else "downtrend" if p < f < sl else "mixed"


def evaluate_technical(df: pd.DataFrame | None, cfg: TechnicalCriteria) -> CriterionResult:
    if df is None or len(df) < 60:
        return CriterionResult("technical", Outcome.MISSING, cfg.hard, 0.0,
                               "Insufficient price history (need ≥ 60 daily bars).", {})
    a = analyze(df, cfg)
    k, d = a["classification"], a["support_distance_pct"]
    score = CLASS_SCORE[k]
    if a["primary_support"]:
        score = min(100, score + 5 * (a["primary_support"]["touches"] - cfg.min_zone_touches))
    if a["rsi14"] is not None and a["rsi14"] > 70:
        score *= 0.8
    if a["buying_volume_ratio"] and a["buying_volume_ratio"] > 1.2:
        score = min(100, score + 5)
    ok = k != "broken_support" and d is not None and d <= cfg.support_proximity_pct
    if k == "broken_support":
        expl = (f"Fails: price ${a['price']:.2f} broke below validated support "
                f"{a['broken_support']['low']:.2f}–{a['broken_support']['high']:.2f} without a reclaim.")
    elif d is None:
        expl = "Fails: no validated support zone (≥2 touches) found below the current price."
    else:
        z = a["primary_support"]
        expl = (f"{'Passes' if ok else 'Fails'}: price ${a['price']:.2f} is {d:.2f}% above the support zone "
                f"${z['low']:.2f}–${z['high']:.2f} ({z['touches']} touches; confluence: {', '.join(z['sources'])}). "
                f"Classification: {k.replace('_', ' ')}; threshold {cfg.support_proximity_pct}%. Trend: {a['trend']}.")
    return CriterionResult("technical", Outcome.PASS if ok else Outcome.FAIL, cfg.hard, float(score), expl, a)
