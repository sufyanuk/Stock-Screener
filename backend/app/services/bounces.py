"""Bounce screens over every listed US common stock, from API daily bars.

Data: Alpaca's multi-symbol daily-bars endpoint (consolidated SIP feed, split/dividend adjusted)
returns a year of bars for 100 stocks per request, so the whole market loads in a couple of minutes;
afterwards each refresh adds only the newest sessions. Bars are folded oldest-first into a compact
rolling state per stock (``price_snapshots.data``):
  * the last 252 daily lows (52-week low), the last 51 volumes, the last 15 bars;
  * the 50-day EMA (seeded with a 50-day simple average, then 2/51 smoothing);
  * the 14-day RSI (Wilder smoothing).
The first load is resumable across calls (time budget per call); each daily scan then adds the newest
session, and a stock with a split-sized jump in a new bar has its whole history reloaded.

52-week-low bounce (all must hold, on the latest session):
  * price below ``max_price`` (default $100);
  * close within ``near_low_pct`` (5%) of the 52-week low and at least ``min_recover_pct`` (2%) above it;
  * volume greater than ``vol_mult`` (1.5×) the 50-day average volume (previous 50 sessions);
  * optional: RSI(14) between 30 and 50.
50-EMA bounce (all must hold, on the latest session):
  * price below ``max_price`` (default $100);
  * the day's low came within ``touch_pct`` (1%) of the 50-day EMA (or below it);
  * the close is above the 50-day EMA;
  * volume at least ``vol_mult`` (1.5×) the 20-day average volume (previous 20 sessions);
  * optional: RSI(14) between 40 and 60.
Missing data never passes. Research only — a bounce can fail.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app import market_calendar as mc
from app.models import MarketDay, PriceSnapshot
from app.providers.http import ProviderError

TICKER_RE = re.compile(r"^[A-Z]{1,5}$")  # common stocks (no warrants/units/preferreds)
HISTORY_SESSIONS = 260
K = 2 / 51


def _new_state() -> dict:
    return {"days": [], "lows": [], "vols": [], "bars": [], "closes": [], "ema": None, "emah": [],
            "rsi": {"n": 0, "sg": 0.0, "sl": 0.0, "ag": None, "al": None, "pc": None}, "rsih": []}


def update_state(st: dict, day: str, o: float, h: float, lo: float, c: float, v: float) -> None:
    """Fold one daily bar into a stock's rolling state (bars must arrive oldest-first)."""
    if st["days"] and day <= st["days"][-1]:
        return
    st["days"] = (st["days"] + [day])[-252:]
    st["lows"] = (st["lows"] + [lo])[-252:]
    st["vols"] = (st["vols"] + [v])[-51:]
    st["bars"] = (st["bars"] + [[day, o, h, lo, c, v]])[-15:]
    # 50-day EMA
    if st["ema"] is None:
        st["closes"] = (st["closes"] + [c])[-50:]
        if len(st["closes"]) == 50:
            st["ema"] = sum(st["closes"]) / 50
            st["closes"] = []
    else:
        st["ema"] = c * K + st["ema"] * (1 - K)
    st["emah"] = (st["emah"] + [st["ema"]])[-15:]
    # RSI(14), Wilder
    r = st["rsi"]
    if r["pc"] is not None:
        ch = c - r["pc"]
        g, l_ = max(ch, 0.0), max(-ch, 0.0)
        r["n"] += 1
        if r["n"] <= 14:
            r["sg"] += g
            r["sl"] += l_
            if r["n"] == 14:
                r["ag"], r["al"] = r["sg"] / 14, r["sl"] / 14
        else:
            r["ag"] = (r["ag"] * 13 + g) / 14
            r["al"] = (r["al"] * 13 + l_) / 14
    r["pc"] = c
    rsi = None
    if r["ag"] is not None:
        rsi = 100.0 if r["al"] == 0 else 100 - 100 / (1 + r["ag"] / r["al"])
    st["rsih"] = (st["rsih"] + [rsi])[-15:]


SOURCE = "alpaca"
BATCH = 100          # symbols per multi-symbol request
PARALLEL = 4         # batches fetched at the same time
HISTORY_DAYS = 400   # calendar days of history for a first load (~275 sessions)


def _target_days(today: date | None = None) -> list[date]:
    end = mc.last_completed_session(datetime.now(timezone.utc)) if today is None else today
    return mc.previous_sessions(end, HISTORY_SESSIONS)


def _target() -> date:
    return _target_days()[-1]


def _universe(db: Session) -> list[str]:
    from app.models import Security

    return sorted({t for t in db.scalars(select(Security.ticker)).all() if t and TICKER_RE.match(t)})


def status(db: Session) -> dict:
    target = _target()
    uni = _universe(db)
    rows = db.execute(select(PriceSnapshot.symbol, PriceSnapshot.as_of, PriceSnapshot.close)
                      .where(PriceSnapshot.source == SOURCE)).all()
    have = {r.symbol for r in rows}
    with_data = [r for r in rows if r.close and r.close > 0]
    as_of = max((r.as_of for r in with_data), default=None)
    stale = sum(1 for r in with_data if r.as_of < target)
    remaining = len([s for s in uni if s not in have])
    return {"sessions": HISTORY_SESSIONS if with_data else 0, "first": _target_days()[0].isoformat(),
            "as_of": as_of.isoformat() if as_of else None, "target": target.isoformat(),
            "remaining": remaining, "stale": stale, "universe": len(uni), "symbols": len(with_data),
            "provider": "alpaca (SIP daily bars)" if with_data else None,
            "age_days": (target - as_of).days if as_of else None}


def _state_from(bars: list[tuple]) -> dict:
    st = _new_state()
    for d, o, h, lo, c, v in bars:
        if c and c > 0:
            update_state(st, d, float(o or c), float(h or c), float(lo or c), float(c), float(v or 0))
    return st


def _write(db: Session, states: dict[str, dict], target: date) -> None:
    if not states:
        return
    now = datetime.now(timezone.utc)
    db.execute(delete(PriceSnapshot).where(PriceSnapshot.symbol.in_(list(states))))
    rows = []
    for sym, st in states.items():
        if st.get("days"):
            rows.append({"symbol": sym, "as_of": date.fromisoformat(st["days"][-1]), "close": st["bars"][-1][4],
                         "data": st, "source": SOURCE, "imported_at": now})
        else:  # no bars from the provider (delisted, no trades): remember it so it is not re-requested
            rows.append({"symbol": sym, "as_of": target, "close": 0.0, "data": {"days": []}, "source": SOURCE,
                         "imported_at": now})
    db.execute(insert(PriceSnapshot), rows)
    db.commit()


async def refresh(db: Session, ds, max_seconds: float = 200.0, rebuild: bool = False) -> dict:
    """Load a year of daily bars for every listed US common stock (Alpaca multi-symbol requests, 100
    stocks each), then keep it current by adding the newest sessions. Resumable within a time budget.
    A stock whose new bar jumps by more than 40% (an unadjusted split) has its history reloaded."""
    import asyncio
    from datetime import timedelta

    started = time.time()
    alp = getattr(ds, "alpaca", None)
    if alp is None or not hasattr(alp, "multi_daily_bars"):
        return {"processed": 0, "errors": ["Alpaca keys are not set (ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY)"]} | status(db)
    target = _target()
    # rows from the earlier whole-market loader (and the old day markers) are replaced
    db.execute(delete(PriceSnapshot).where(PriceSnapshot.source.notin_([SOURCE, "csv"])))
    db.execute(delete(MarketDay))
    if rebuild:
        db.execute(delete(PriceSnapshot).where(PriceSnapshot.source == SOURCE))
    db.commit()
    uni = _universe(db)
    if not uni and hasattr(ds, "universe"):
        try:
            uni = sorted({r["ticker"] for r in await ds.universe() if TICKER_RE.match(r.get("ticker") or "")})
        except ProviderError as e:
            return {"processed": 0, "errors": [str(e)]} | status(db)
    existing = {r.symbol: (r.as_of, r.close) for r in db.execute(
        select(PriceSnapshot.symbol, PriceSnapshot.as_of, PriceSnapshot.close).where(PriceSnapshot.source == SOURCE)).all()}
    new = [s for s in uni if s not in existing]
    stale = [s for s, (a, c) in existing.items() if c and c > 0 and a < target]
    errors: list[str] = []
    done = 0
    hist_start = target - timedelta(days=HISTORY_DAYS)

    def time_left() -> bool:
        return time.time() - started < max_seconds

    # 1) first load: full history for stocks not loaded yet
    batches = [new[i:i + BATCH] for i in range(0, len(new), BATCH)]
    for i in range(0, len(batches), PARALLEL):
        if not time_left():
            break
        group = batches[i:i + PARALLEL]
        results = await asyncio.gather(*(alp.multi_daily_bars(b, hist_start, target) for b in group), return_exceptions=True)
        for b, res in zip(group, results):
            if isinstance(res, Exception):
                errors.append(str(res)[:200])
                continue
            _write(db, {s: _state_from(res.get(s, [])) for s in b}, target)
            done += len(b)
        if errors and not done:
            break
    # 2) daily update: add the sessions after each stock's last bar
    batches = [stale[i:i + BATCH * 2] for i in range(0, len(stale), BATCH * 2)]
    for i in range(0, len(batches), PARALLEL):
        if not time_left():
            break
        group = batches[i:i + PARALLEL]
        frm = [min(existing[s][0] for s in b) + timedelta(days=1) for b in group]
        results = await asyncio.gather(*(alp.multi_daily_bars(b, f, target) for b, f in zip(group, frm)), return_exceptions=True)
        for b, res in zip(group, results):
            if isinstance(res, Exception):
                errors.append(str(res)[:200])
                continue
            states = {s.symbol: s.data for s in db.scalars(select(PriceSnapshot).where(PriceSnapshot.symbol.in_(b))).all()}
            reload = []
            for sym in b:
                st, bars = states.get(sym), res.get(sym, [])
                if not st or not st.get("days"):
                    continue
                prev = st["bars"][-1][4]
                for d, o, h, lo, c, v in bars:
                    if d <= st["days"][-1] or not c:
                        continue
                    if prev and not 0.6 < c / prev < 1.67:  # probable split: history must be re-adjusted
                        reload.append(sym)
                        break
                    update_state(st, d, float(o or c), float(h or c), float(lo or c), float(c), float(v or 0))
                    prev = c
            if reload:
                try:
                    full = await alp.multi_daily_bars(reload, hist_start, target)
                    for sym in reload:
                        states[sym] = _state_from(full.get(sym, []))
                except ProviderError as e:
                    errors.append(str(e)[:200])
                    for sym in reload:
                        states.pop(sym, None)
            _write(db, {s: st for s, st in states.items() if s in b}, target)
            done += len(b)
    return {"processed": done, "errors": errors[:3]} | status(db)


def _metrics(st: dict) -> dict | None:
    if not st.get("bars"):
        return None
    d, o, h, lo, c, v = st["bars"][-1]
    prev = st["bars"][-2][4] if len(st["bars"]) > 1 else None
    vols = st.get("vols") or []
    prior = vols[:-1]
    avg50 = sum(prior[-50:]) / len(prior[-50:]) if len(prior) >= 20 else None
    avg20 = sum(prior[-20:]) / 20 if len(prior) >= 20 else None
    lows = st.get("lows") or []
    low52 = min(lows) if len(lows) >= 200 else None
    low_i = lows.index(low52) if low52 is not None else None
    ema = st.get("ema")
    rsi = (st.get("rsih") or [None])[-1]
    return {"date": d, "open": o, "high": h, "low": lo, "price": c, "volume": v,
            "day_change_pct": (c / prev - 1) * 100 if prev else None,
            "avg_vol50": avg50, "avg_vol20": avg20, "low52": low52,
            "low52_date": st["days"][low_i] if low_i is not None else None,
            "above_low52_pct": (c / low52 - 1) * 100 if low52 else None,
            "ema50": ema, "vs_ema50_pct": (c / ema - 1) * 100 if ema else None,
            "low_vs_ema_pct": (lo / ema - 1) * 100 if ema else None, "rsi": rsi}


def low52_bounce(m: dict, p: dict) -> bool:
    if m["low52"] is None or m["avg_vol50"] in (None, 0):
        return False
    up = m["above_low52_pct"]
    if not (p["min_recover_pct"] <= up <= p["near_low_pct"]):
        return False
    if not m["volume"] > p["vol_mult"] * m["avg_vol50"]:
        return False
    if p["use_rsi"] and (m["rsi"] is None or not p["rsi_low_min"] <= m["rsi"] <= p["rsi_low_max"]):
        return False
    return True


def ema50_bounce(m: dict, p: dict) -> bool:
    e = m["ema50"]
    if e is None or m["avg_vol20"] in (None, 0):
        return False
    if not m["low"] <= e * (1 + p["touch_pct"] / 100):
        return False
    if not m["price"] > e:
        return False
    if not m["volume"] >= p["vol_mult"] * m["avg_vol20"]:
        return False
    if p["use_rsi"] and (m["rsi"] is None or not p["rsi_ema_min"] <= m["rsi"] <= p["rsi_ema_max"]):
        return False
    return True


def screen(db: Session, kind: str, p: dict) -> dict:
    info = status(db)
    fn = low52_bounce if kind == "low52" else ema50_bounce
    rows = []
    for s in db.scalars(select(PriceSnapshot).where(PriceSnapshot.source == SOURCE,
                                                    PriceSnapshot.close < p["max_price"])).all():
        if info["as_of"] and s.as_of.isoformat() != info["as_of"]:
            continue  # not traded in the latest session
        m = _metrics(s.data)
        if not m or (p.get("min_price") is not None and m["price"] < p["min_price"]):
            continue
        if fn(m, p):
            avg = m["avg_vol50"] if kind == "low52" else m["avg_vol20"]
            rows.append({"symbol": s.symbol, **m, "vol_ratio": m["volume"] / avg if avg else None})
    rows.sort(key=lambda r: -(r["vol_ratio"] or 0))
    return {"kind": kind, "params": p, "rows": rows[: p.get("limit", 500)], "total": len(rows), "data": info}


def lookup(db: Session, symbols: list[str]) -> dict[str, dict]:
    """Latest bars and levels for the custom watchlist."""
    if not symbols:
        return {}
    out = {}
    for s in db.scalars(select(PriceSnapshot).where(PriceSnapshot.symbol.in_(symbols),
                                                    PriceSnapshot.source == SOURCE)).all():
        m = _metrics(s.data) or {}
        m["bars"] = [{"date": b[0], "open": b[1], "high": b[2], "low": b[3], "close": b[4], "volume": b[5]}
                     for b in s.data.get("bars", [])]
        m["below_high52_pct"] = None
        out[s.symbol] = m
    return out
