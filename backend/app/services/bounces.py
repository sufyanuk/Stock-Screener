"""Bounce screens over the whole US market, from API daily bars.

Data: one "grouped daily" request per trading session returns the OHLCV of every US stock
(Massive; FMP bulk end-of-day as fallback). Sessions are folded in oldest-first into a compact
rolling state per stock (``price_snapshots.data``):
  * the last 252 daily lows (52-week low), the last 51 volumes, the last 15 bars;
  * the 50-day EMA (seeded with a 50-day simple average, then 2/51 smoothing);
  * the 14-day RSI (Wilder smoothing).
The first load needs ~260 sessions (one request each, resumable across calls); each daily scan
then adds the newest session.

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

from sqlalchemy import delete, func, insert, select
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


def _target_days(today: date | None = None) -> list[date]:
    end = mc.last_completed_session(datetime.now(timezone.utc)) if today is None else today
    return mc.previous_sessions(end, HISTORY_SESSIONS)


def status(db: Session) -> dict:
    n, last, first = db.execute(select(func.count(), func.max(MarketDay.day), func.min(MarketDay.day))).one()
    targets = _target_days()
    have = set(db.scalars(select(MarketDay.day).where(MarketDay.day >= targets[0])).all())
    remaining = len([d for d in targets if d not in have and (not last or d > last)])
    provider = db.scalars(select(MarketDay.provider).order_by(MarketDay.day.desc()).limit(1)).first()
    symbols = db.scalar(select(func.count()).select_from(PriceSnapshot).where(PriceSnapshot.source == "api")) or 0
    return {"sessions": n or 0, "first": first.isoformat() if first else None, "as_of": last.isoformat() if last else None,
            "remaining": remaining, "symbols": symbols, "provider": provider,
            "age_days": (mc.last_completed_session(datetime.now(timezone.utc)) - last).days if last else None}


async def refresh(db: Session, ds, max_seconds: float = 200.0, rebuild: bool = False) -> dict:
    """Fold missing sessions (oldest first) into the per-stock state, within a time budget."""
    start = time.time()
    if rebuild:
        db.execute(delete(MarketDay))
        db.execute(delete(PriceSnapshot))
        db.commit()
    targets = _target_days()
    last = db.scalar(select(func.max(MarketDay.day)))
    if last is None:  # first API load replaces any earlier CSV-derived rows
        db.execute(delete(PriceSnapshot))
        db.commit()
    todo = [d for d in targets if last is None or d > last]
    if not todo:
        return {"processed": 0, "errors": []} | status(db)
    states = {s.symbol: s.data for s in db.scalars(select(PriceSnapshot).where(PriceSnapshot.source == "api")).all()}
    known = set(states)
    processed, errors, provider = [], [], None
    for d in todo:
        if time.time() - start > max_seconds:
            break
        try:
            bars, prov = await ds.market_bars(d)
        except ProviderError as e:
            errors.append(str(e))
            break
        if not bars:  # not published yet (or holiday): retry on a later call
            errors.append(f"no daily bars for {d.isoformat()} yet")
            break
        provider = prov.provider
        ds_ = d.isoformat()
        n = 0
        for sym, (o, h, lo, c, v) in bars.items():
            if not TICKER_RE.match(sym) or not c or c <= 0:
                continue
            st = states.get(sym)
            if st is None:
                st = states[sym] = _new_state()
            update_state(st, ds_, float(o or c), float(h or c), float(lo or c), float(c), float(v or 0))
            n += 1
        processed.append((d, n))
    if processed:
        now = datetime.now(timezone.utc)
        rows = [{"symbol": s, "as_of": date.fromisoformat(st["days"][-1]), "close": st["bars"][-1][4], "data": st,
                 "source": "api", "imported_at": now} for s, st in states.items() if st["days"]]
        if known:
            db.execute(delete(PriceSnapshot).where(PriceSnapshot.source == "api"))
        for i in range(0, len(rows), 1000):
            db.execute(insert(PriceSnapshot), rows[i:i + 1000])
        db.execute(insert(MarketDay), [{"day": d, "symbols": n, "provider": provider or "?", "processed_at": now}
                                       for d, n in processed])
        db.commit()
    return {"processed": len(processed), "errors": errors[:3]} | status(db)


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
    for s in db.scalars(select(PriceSnapshot).where(PriceSnapshot.source == "api",
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
                                                    PriceSnapshot.source == "api")).all():
        m = _metrics(s.data) or {}
        m["bars"] = [{"date": b[0], "open": b[1], "high": b[2], "low": b[3], "close": b[4], "volume": b[5]}
                     for b in s.data.get("bars", [])]
        m["below_high52_pct"] = None
        out[s.symbol] = m
    return out
