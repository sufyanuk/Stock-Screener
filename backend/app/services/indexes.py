"""Index constituents for the Seasonality index filter (S&P 500, Nasdaq-100, Dow Jones 30).

Source order: FMP constituent endpoints (when the plan includes them), else the constituent tables on
Wikipedia. Cached in ``index_members`` and refreshed when older than 7 days.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.models import IndexMember
from app.providers.openinsider import _TableParser

INDEXES = {
    "sp500": {"label": "S&P 500", "fmp": "/sp500-constituent",
              "wiki": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "min": 400},
    "ndx": {"label": "Nasdaq-100", "fmp": "/nasdaq-constituent",
            "wiki": "https://en.wikipedia.org/wiki/Nasdaq-100", "min": 90},
    "dji": {"label": "Dow Jones 30", "fmp": "/dowjones-constituent",
            "wiki": "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average", "min": 25},
}
TTL = timedelta(days=7)
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def _norm(sym: str) -> str:
    return sym.strip().upper().replace("-", ".")  # BRK-B (FMP) / BRK.B (Wikipedia) -> BRK.B


def parse_wiki(html: str, min_rows: int) -> list[tuple[str, str | None]]:
    """The first table with a Symbol/Ticker column and at least ``min_rows`` rows."""
    p = _TableParser()
    p.feed(html)
    for t in p.tables:
        if not t["rows"]:
            continue
        head = [c[0].strip().lower() for c in t["rows"][0]]
        si = next((i for i, h in enumerate(head) if h in ("symbol", "ticker", "ticker symbol")), None)
        if si is None:
            continue
        ni = next((i for i, h in enumerate(head) if h in ("security", "company", "name")), None)
        out = []
        for r in t["rows"][1:]:
            if si < len(r):
                sym = _norm(r[si][0])
                if SYMBOL_RE.match(sym):
                    out.append((sym, r[ni][0] if ni is not None and ni < len(r) else None))
        if len(out) >= min_rows:
            return out
    return []


async def _fetch(ds, code: str) -> tuple[list[tuple[str, str | None]], str]:
    cfg = INDEXES[code]
    fmp = getattr(ds, "fmp", None)
    if fmp is not None:
        try:
            j = await fmp.http.get_json(cfg["fmp"], {"apikey": fmp.key}, cache_ttl=0)
            rows = [(_norm(r["symbol"]), r.get("name")) for r in (j if isinstance(j, list) else []) if r.get("symbol")]
            if len(rows) >= cfg["min"]:
                return rows, "fmp"
        except Exception:  # noqa: BLE001 - plan may not include it; fall back to Wikipedia
            pass
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                 headers={"User-Agent": "StockScreener/1.0 (personal research)"}) as c:
        r = await c.get(cfg["wiki"])
    r.raise_for_status()
    rows = parse_wiki(r.text, cfg["min"])
    if not rows:
        raise ValueError(f"could not read the {cfg['label']} constituents table")
    return rows, "wikipedia"


async def members(db: Session, ds, code: str) -> set[str]:
    """Constituent symbols for ``code`` (refreshed when older than 7 days; stale list kept on failure)."""
    if code not in INDEXES:
        return set()
    last = db.scalar(select(func.max(IndexMember.retrieved_at)).where(IndexMember.index_code == code))
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if last is None or datetime.now(timezone.utc) - last > TTL:
        try:
            rows, src = await _fetch(ds, code)
            db.execute(delete(IndexMember).where(IndexMember.index_code == code))
            now = datetime.now(timezone.utc)
            seen = {}
            for sym, name in rows:
                seen.setdefault(sym, name)
            db.execute(insert(IndexMember), [{"index_code": code, "symbol": s, "name": (n or None) and n[:255],
                                              "source": src, "retrieved_at": now} for s, n in seen.items()])
            db.commit()
        except Exception:  # noqa: BLE001 - keep the previous list, if any
            db.rollback()
    return set(db.scalars(select(IndexMember.symbol).where(IndexMember.index_code == code)).all())
