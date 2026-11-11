"""Market pulse for the Dashboard: S&P 500 breadth, Market Mood, Macro backdrop and the next Fed decision.

Computed by the nightly GitHub job and stored (kv_store "market_pulse"); the Dashboard only reads it.

* **Breadth** — S&P 500 members up / down / unchanged on the day, from SentiSense's market heatmap
  (free tier). Backup: Alpaca snapshots (price vs previous close).
* **Market Mood (0–100)** — SentiSense's Market Mood (/api/v2/market-mood, free tier): social sentiment, market
  direction, risk appetite, social momentum, S&P 500 trend and options flow; phases Extreme Fear ≤15, Fear ≤30,
  Anxiety ≤45, Neutral ≤55, Optimism ≤70, Greed ≤85, Extreme Greed. SentiSense has no macro score or Fed
  calendar, so those two stay on FRED and federalreserve.gov.
  Backup when SentiSense fails: the app's own breadth-based gauge: the average of
  (1) advancers as % of advancers + decliners, (2) % of members above their 50-day average,
  (3) % above their 200-day average, (4) members near 52-week highs as % of those near highs or lows
  (within 2%), (5) S&P 500 (SPY) versus its 125-day average, −10%…+10% mapped to 0…100.
  0–24 Extreme Fear · 25–44 Fear · 45–55 Neutral · 56–75 Greed · 76–100 Extreme Greed.
* **Macro backdrop (−5…+5)** — five FRED series, each −1 / 0 / +1: 10-year yield change over 3 months
  (rising > 0.25 pt −1, falling +1); 10y−2y curve (inverted −1, above 0.5 pt +1); high-yield credit spread
  change over 3 months (widening > 0.3 pt −1, narrowing +1); broad dollar index change over 3 months (> +2% −1,
  < −2% +1); fed funds rate change over 6 months (> +0.25 pt −1, < −0.25 pt +1).
  ≤ −2 Hostile · −1…+1 Neutral · ≥ +2 Supportive. Needs a free FRED API key (FRED_API_KEY).
* **Fed** — the next FOMC decision day (second day of the meeting) from federalreserve.gov's calendar,
  with the published 2026–2027 schedule as a fallback.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

import httpx
import pandas as pd
from sqlalchemy.orm import Session

from app.config import get_settings

log = logging.getLogger(__name__)
KEY = "market_pulse"
UA = {"User-Agent": "StockScreener/1.0 (personal research app)"}
FOMC_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
# decision days (second day of each meeting) published by the Fed, used if the calendar page cannot be read
FOMC_FALLBACK = [date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17), date(2026, 7, 29),
                 date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9), date(2027, 1, 27), date(2027, 3, 17),
                 date(2027, 4, 28), date(2027, 6, 9), date(2027, 7, 28), date(2027, 9, 15), date(2027, 10, 27),
                 date(2027, 12, 8)]
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}


# ------------------------------------------------------------------ labels
def mood_label(score: float) -> str:
    return ("Extreme Fear" if score < 25 else "Fear" if score < 45 else "Neutral" if score <= 55
            else "Greed" if score <= 75 else "Extreme Greed")


def macro_label(score: int) -> str:
    return "Hostile" if score <= -2 else "Supportive" if score >= 2 else "Neutral"


# ------------------------------------------------------------------ breadth and mood
def breadth(changes: dict[str, float | None]) -> dict:
    vals = [c for c in changes.values() if c is not None]
    adv = sum(1 for c in vals if c > 0.0005)
    dec = sum(1 for c in vals if c < -0.0005)
    return {"advancing": adv, "declining": dec, "unchanged": len(vals) - adv - dec, "total": len(vals)}


def mood(br: dict, closes: dict[str, pd.Series], spy: pd.Series | None) -> dict:
    """Breadth-based gauge from the day's breadth and each member's daily closes (oldest first)."""
    comps: list[dict] = []
    if br["advancing"] + br["declining"]:
        comps.append({"name": "Advancing stocks", "value": br["advancing"] / (br["advancing"] + br["declining"]) * 100})
    above50 = above200 = highs = lows = n50 = n200 = 0
    for s in closes.values():
        s = s.dropna()
        if len(s) >= 50:
            n50 += 1
            above50 += s.iloc[-1] > s.iloc[-50:].mean()
        if len(s) >= 200:
            n200 += 1
            above200 += s.iloc[-1] > s.iloc[-200:].mean()
        if len(s) >= 120:
            win = s.iloc[-252:]
            highs += s.iloc[-1] >= win.max() * 0.98
            lows += s.iloc[-1] <= win.min() * 1.02
    if n50:
        comps.append({"name": "Above 50-day average", "value": above50 / n50 * 100})
    if n200:
        comps.append({"name": "Above 200-day average", "value": above200 / n200 * 100})
    if highs + lows:
        comps.append({"name": "Near 52-week highs vs lows", "value": highs / (highs + lows) * 100})
    if spy is not None and len(spy.dropna()) >= 125:
        s = spy.dropna()
        gap = (s.iloc[-1] / s.iloc[-125:].mean() - 1) * 100
        comps.append({"name": "S&P 500 vs 125-day average", "value": max(0.0, min(100.0, 50 + gap * 5)),
                      "detail": f"{gap:+.1f}%"})
    if not comps:
        return {"score": None, "label": None, "components": []}
    score = round(sum(c["value"] for c in comps) / len(comps))
    for c in comps:
        c["value"] = round(c["value"], 1)
    return {"score": score, "label": mood_label(score), "components": comps}


# ------------------------------------------------------------------ macro (FRED)
FRED_SERIES = {"DGS10": "10-year Treasury yield", "T10Y2Y": "Yield curve (10y − 2y)",
               "BAMLH0A0HYM2": "High-yield credit spread", "DTWEXBGS": "US dollar (broad index)",
               "DFF": "Fed funds rate"}


def _value_at(s: pd.Series, when: date) -> float | None:
    s = s[s.index <= pd.Timestamp(when)].dropna()
    return float(s.iloc[-1]) if len(s) else None


def macro(series: dict[str, pd.Series], today: date) -> dict:
    """Each component −1 / 0 / +1 (see module docstring); missing series are left out."""
    comps: list[dict] = []

    def add(name, score, detail):
        comps.append({"name": name, "score": score, "detail": detail})

    m3, m6 = today - timedelta(days=91), today - timedelta(days=182)
    if (s := series.get("DGS10")) is not None and (now := _value_at(s, today)) is not None and (then := _value_at(s, m3)) is not None:
        d = now - then
        add(FRED_SERIES["DGS10"], -1 if d > 0.25 else 1 if d < -0.25 else 0, f"{now:.2f}% ({d:+.2f} pt in 3 months)")
    if (s := series.get("T10Y2Y")) is not None and (now := _value_at(s, today)) is not None:
        add(FRED_SERIES["T10Y2Y"], -1 if now < 0 else 1 if now > 0.5 else 0, f"{now:+.2f} pt" + (" (inverted)" if now < 0 else ""))
    if (s := series.get("BAMLH0A0HYM2")) is not None and (now := _value_at(s, today)) is not None and (then := _value_at(s, m3)) is not None:
        d = now - then
        add(FRED_SERIES["BAMLH0A0HYM2"], -1 if d > 0.3 else 1 if d < -0.3 else 0, f"{now:.2f} pt ({d:+.2f} in 3 months)")
    if (s := series.get("DTWEXBGS")) is not None and (now := _value_at(s, today)) is not None and (then := _value_at(s, m3)):
        d = (now / then - 1) * 100
        add(FRED_SERIES["DTWEXBGS"], -1 if d > 2 else 1 if d < -2 else 0, f"{d:+.1f}% in 3 months")
    if (s := series.get("DFF")) is not None and (now := _value_at(s, today)) is not None and (then := _value_at(s, m6)) is not None:
        d = now - then
        add(FRED_SERIES["DFF"], -1 if d > 0.25 else 1 if d < -0.25 else 0, f"{now:.2f}% ({d:+.2f} pt in 6 months)")
    if not comps:
        return {"score": None, "label": None, "components": []}
    score = sum(c["score"] for c in comps)
    return {"score": score, "label": macro_label(score), "components": comps}


async def fred_series(client: httpx.AsyncClient, key: str, sid: str, start: date) -> pd.Series:
    r = await client.get("https://api.stlouisfed.org/fred/series/observations",
                         params={"series_id": sid, "api_key": key, "file_type": "json", "observation_start": start.isoformat()})
    r.raise_for_status()
    obs = r.json().get("observations") or []
    vals = {pd.Timestamp(o["date"]): float(o["value"]) for o in obs if o.get("value") not in (None, "", ".")}
    return pd.Series(vals, dtype=float).sort_index()


# ------------------------------------------------------------------ Fed calendar
def parse_fomc(html: str) -> list[date]:
    """Decision days (last day of each scheduled meeting) from the Fed's FOMC calendar page."""
    out: list[date] = []
    heads = list(re.finditer(r"(\d{4}) FOMC Meetings", html))
    for i, m in enumerate(heads):
        year = int(m.group(1))
        seg = html[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(html)]
        months = re.findall(r"fomc-meeting__month[^>]*>\s*(?:<strong>)?\s*([^<]+?)\s*<", seg)
        days = re.findall(r"fomc-meeting__date[^>]*>\s*([^<]+?)\s*<", seg)
        for mon, d in zip(months, days):
            if not re.match(r"^\d{1,2}(-\d{1,2})?\*?$", d.strip()):
                continue  # notation votes, unscheduled meetings
            last_day = int(d.strip().rstrip("*").split("-")[-1])
            name = mon.lower().split("/")[-1].strip()  # "April/May" -> the second month holds the last day
            if name in MONTHS:
                try:
                    out.append(date(year, MONTHS[name], last_day))
                except ValueError:
                    continue
    return sorted(set(out))


def next_fed(dates: list[date], today: date) -> date | None:
    return next((d for d in sorted(dates) if d >= today), None)


# ------------------------------------------------------------------ nightly computation
async def _own_mood_breadth(db: Session, ds, alp, today: date, out: dict) -> None:
    """Backup when SentiSense is unavailable: the app's own breadth and Market Mood from Alpaca prices."""
    from app.services import indexes as ix

    try:
        members = sorted(await ix.members(db, ds, "sp500"))
        if not members:
            raise RuntimeError("S&P 500 member list unavailable")
        if alp is None:
            raise RuntimeError("Alpaca keys are not set")
        snaps = await alp.multi_snapshots(members)
        changes = {s: (snaps.get(s) or {}).get("day_change_pct") for s in members}
        br = breadth(changes)
        br["as_of"] = max((v.get("market_ts").date().isoformat() for v in snaps.values() if v.get("market_ts")), default=None)
        if not out.get("breadth"):
            out["breadth"] = br | {"source": "Alpaca"}
        if not out.get("mood"):  # a year of bars is only needed for the app's own mood
            bars = await alp.multi_daily_bars(members + ["SPY"], today - timedelta(days=400), today, "1Day")
            closes = {s: pd.Series([b[4] for b in rows], dtype=float) for s, rows in bars.items() if rows}
            out["mood"] = mood(br, {s: c for s, c in closes.items() if s != "SPY"}, closes.get("SPY")) | {"source": "app's own"}
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"breadth/mood: {str(e)[:200]}")


async def compute(db: Session, ds, today: date | None = None) -> dict:
    """Everything independently: a part that fails is left empty with its error, never invented."""
    from app.db import upsert
    from app.models import KeyValue
    today = today or date.today()
    out: dict = {"computed_at": datetime.now(timezone.utc).isoformat(), "errors": []}
    alp = getattr(ds, "alpaca", None)
    ss = getattr(ds, "sentisense", None)
    if ss is not None:  # SentiSense first: its Market Mood and S&P 500 breadth (free tier, 2 requests)
        try:
            out["mood"] = await ss.market_mood()
            if out["mood"]:
                out["mood"]["source"] = "SentiSense"
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"SentiSense mood: {str(e)[:150]}")
        try:
            br = await ss.market_breadth("sp500")
            stale = br and br.get("as_of") and date.fromisoformat(br["as_of"]) < today - timedelta(days=4)
            if stale:  # an old snapshot must not be shown as today's tape: use the backup instead
                out["errors"].append(f"SentiSense breadth snapshot is from {br['as_of']}; used Alpaca instead")
            elif br:
                out["breadth"] = br | {"source": "SentiSense"}
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"SentiSense breadth: {str(e)[:150]}")
    if not out.get("mood") or not out.get("breadth"):
        await _own_mood_breadth(db, ds, alp, today, out)
    key = get_settings().fred_api_key
    async with httpx.AsyncClient(timeout=30, headers=UA) as client:
        if key:
            series = {}
            for sid in FRED_SERIES:
                try:
                    series[sid] = await fred_series(client, key, sid, today - timedelta(days=200))
                except Exception as e:  # noqa: BLE001
                    out["errors"].append(f"FRED {sid}: {str(e)[:150]}")
            out["macro"] = macro(series, today)
        else:
            out["macro"] = {"score": None, "label": None, "components": [],
                            "missing": "Add a free FRED API key (FRED_API_KEY) to show the macro backdrop."}
        dates: list[date] = []
        try:
            r = await client.get(FOMC_URL)
            r.raise_for_status()
            dates = parse_fomc(r.text)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"Fed calendar: {str(e)[:150]}")
        nxt = next_fed(dates, today) if dates else None
        source = "federalreserve.gov"
        if nxt is None:
            nxt, source = next_fed(FOMC_FALLBACK, today), "published 2026–2027 schedule"
        out["fed"] = {"next_decision": nxt.isoformat() if nxt else None, "source": source}
    upsert(db, KeyValue, [{"key": KEY, "value": out, "updated_at": datetime.now(timezone.utc)}])
    db.commit()
    return out


def stored(db: Session) -> dict | None:
    from app.models import KeyValue

    row = db.get(KeyValue, KEY)
    return dict(row.value) if row else None
