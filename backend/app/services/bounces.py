"""Bounce screens over every stock in the imported price-history CSV.

The browser reduces the CSV to a compact snapshot per stock (last 15 daily bars, the 50-day EMA for
those bars, the 52-week low/high and 50-day average volume) and uploads only that.

52-week-low bounce (all must hold):
  * the 52-week low (lowest low of the last 252 sessions, ≥ 200 sessions of history) was set
    between 1 and ``within_days`` sessions ago — i.e. recently, but not today;
  * the latest close is between ``min_bounce_pct`` and ``max_bounce_pct`` above that low;
  * optionally, the latest close is above the previous close (an up day).

50-EMA bounce (all must hold):
  * the EMA is rising (today's EMA above the EMA 10 sessions ago) when ``require_rising`` is set;
  * within the last ``within_days`` sessions a daily low came within ``touch_pct`` of the EMA
    (or dipped below it) while no close in the last 10 sessions fell more than ``touch_pct`` below it;
  * the latest close is above the EMA but not more than ``max_above_pct`` above it;
  * optionally, the latest close is above the previous close.
Both screens: price range and minimum 50-day average volume filters. Missing data never passes.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import PriceSnapshot

SERIES = ("d", "o", "h", "l", "c", "v", "ema50")


def _clean(s: dict) -> dict | None:
    try:
        sym = str(s["symbol"]).strip().upper()
        d = [str(x)[:10] for x in s["d"]]
        c = [float(x) for x in s["c"]]
        if not sym or not 1 <= len(sym) <= 16 or not d or len(c) != len(d) or not c[-1] > 0:
            return None
        date.fromisoformat(d[-1])
        out = {"symbol": sym, "d": d, "c": c}
        for k in ("o", "h", "l", "v"):
            out[k] = [None if x is None else float(x) for x in s.get(k) or [None] * len(d)][-len(d):]
        e = s.get("ema50")
        out["ema50"] = [None if x is None else float(x) for x in e][-len(d):] if e else None
        for k in ("low52", "high52", "avg_vol50"):
            out[k] = float(s[k]) if s.get(k) is not None else None
        for k in ("low52_date", "high52_date"):
            out[k] = str(s[k])[:10] if s.get(k) else None
        out["low52_ago"] = int(s["low52_ago"]) if s.get("low52_ago") is not None else None
        out["n"] = int(s.get("n") or len(d))
        return out
    except (KeyError, TypeError, ValueError):
        return None


def import_snapshots(db: Session, items: list[dict], replace_all: bool, source: str) -> dict:
    clean = {c["symbol"]: c for c in (_clean(s) for s in items) if c}
    if replace_all:
        db.execute(delete(PriceSnapshot))
    elif clean:
        db.execute(delete(PriceSnapshot).where(PriceSnapshot.symbol.in_(clean)))
    now = datetime.now(timezone.utc)
    for sym, c in clean.items():
        db.add(PriceSnapshot(symbol=sym, as_of=date.fromisoformat(c["d"][-1]), close=c["c"][-1], data=c,
                             source=source[:64], imported_at=now))
    db.commit()
    return {"stored": len(clean), "skipped": len(items) - len(clean)}


def snapshot_info(db: Session) -> dict:
    n, as_of, imported = db.execute(select(func.count(), func.max(PriceSnapshot.as_of),
                                           func.max(PriceSnapshot.imported_at))).one()
    src = db.scalars(select(PriceSnapshot.source).order_by(PriceSnapshot.imported_at.desc()).limit(1)).first()
    from app.services.research import today_ny

    return {"symbols": n or 0, "as_of": as_of.isoformat() if as_of else None, "source": src,
            "age_days": (today_ny() - as_of).days if as_of else None,
            "imported_at": imported.isoformat() if imported else None}


def _base(d: dict, as_of: date | None) -> dict:
    c = d["c"]
    prev = c[-2] if len(c) > 1 else None
    return {"symbol": d["symbol"], "date": d["d"][-1], "price": c[-1],
            "day_change_pct": (c[-1] / prev - 1) * 100 if prev else None,
            "avg_vol50": d.get("avg_vol50"), "low52": d.get("low52"), "low52_date": d.get("low52_date"),
            "high52": d.get("high52"), "high52_date": d.get("high52_date"),
            "below_high52_pct": (c[-1] / d["high52"] - 1) * 100 if d.get("high52") else None,
            "stale": bool(as_of and d["d"][-1] < as_of.isoformat())}


def _common_ok(d: dict, p: dict) -> bool:
    px = d["c"][-1]
    if p.get("min_price") is not None and px < p["min_price"]:
        return False
    if p.get("max_price") is not None and px >= p["max_price"]:
        return False
    if p.get("min_avg_volume"):
        if d.get("avg_vol50") is None or d["avg_vol50"] < p["min_avg_volume"]:
            return False
    if p.get("up_day") and (len(d["c"]) < 2 or not d["c"][-1] > d["c"][-2]):
        return False
    return True


def low52_bounce(d: dict, p: dict) -> dict | None:
    low, ago = d.get("low52"), d.get("low52_ago")
    if low is None or ago is None or d.get("n", 0) < 200 or not low > 0:
        return None
    if not 1 <= ago <= p["within_days"]:
        return None
    bounce = (d["c"][-1] / low - 1) * 100
    if not p["min_bounce_pct"] <= bounce <= p["max_bounce_pct"]:
        return None
    return {"bounce_pct": bounce, "sessions_since_low": ago}


def ema50_bounce(d: dict, p: dict) -> dict | None:
    e, c, lows = d.get("ema50"), d["c"], d["l"]
    if not e or len(e) < 11 or e[-1] is None or e[-11] is None or len(c) < 10:
        return None
    if p["require_rising"] and not e[-1] > e[-11]:
        return None
    tol = p["touch_pct"] / 100
    w = p["within_days"]
    touched = [i for i in range(len(c) - w, len(c))
               if e[i] is not None and lows[i] is not None and lows[i] <= e[i] * (1 + tol)]
    if not touched:
        return None
    if any(e[i] is None or c[i] < e[i] * (1 - tol) for i in range(len(c) - 10, len(c))):
        return None
    above = (c[-1] / e[-1] - 1) * 100
    if not 0 < above <= p["max_above_pct"]:
        return None
    return {"ema50": e[-1], "above_ema_pct": above, "sessions_since_touch": len(c) - 1 - touched[-1],
            "ema_change_10d_pct": (e[-1] / e[-11] - 1) * 100}


def screen(db: Session, kind: str, p: dict) -> dict:
    info = snapshot_info(db)
    as_of = date.fromisoformat(info["as_of"]) if info["as_of"] else None
    fn = low52_bounce if kind == "low52" else ema50_bounce
    rows = []
    for snap in db.scalars(select(PriceSnapshot)).all():
        d = snap.data
        if not d or not _common_ok(d, p):
            continue
        hit = fn(d, p)
        if hit:
            rows.append(_base(d, as_of) | hit)
    key = (lambda r: (r["sessions_since_low"], r["bounce_pct"])) if kind == "low52" else \
        (lambda r: (r["sessions_since_touch"], r["above_ema_pct"]))
    rows.sort(key=key)
    return {"kind": kind, "params": p, "rows": rows[: p.get("limit", 500)], "total": len(rows), "data": info}


def lookup(db: Session, symbols: list[str]) -> dict[str, dict]:
    if not symbols:
        return {}
    info = snapshot_info(db)
    as_of = date.fromisoformat(info["as_of"]) if info["as_of"] else None
    out = {}
    for s in db.scalars(select(PriceSnapshot).where(PriceSnapshot.symbol.in_(symbols))).all():
        d = s.data
        b = _base(d, as_of)
        e = (d.get("ema50") or [None])[-1]
        b["ema50"] = e
        b["vs_ema50_pct"] = (d["c"][-1] / e - 1) * 100 if e else None
        b["bars"] = [{"date": d["d"][i], "open": d["o"][i], "high": d["h"][i], "low": d["l"][i], "close": d["c"][i],
                      "volume": d["v"][i]} for i in range(len(d["d"]))]
        b["above_low52_pct"] = (d["c"][-1] / d["low52"] - 1) * 100 if d.get("low52") else None
        out[s.symbol] = b
    return out
