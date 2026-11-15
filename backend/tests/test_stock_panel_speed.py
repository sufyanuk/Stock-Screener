"""The stock side panel opens at once from stored data; live sources are capped and cached."""
import asyncio
import time

from app.services import research as rs


class SlowDS:
    calls = 0

    async def company_quote(self, t):
        SlowDS.calls += 1
        from tests.conftest import prov
        return {"price": 12.5, "name": "Live Name", "pe": 10.0}, prov("finnhub")

    async def analyst(self, t):
        SlowDS.calls += 1
        await asyncio.sleep(30)  # a stuck provider

    async def news(self, t):
        SlowDS.calls += 1
        return [{"title": "x", "url": "u", "published": "2026-11-14", "publisher": "p"}], None

    async def bars(self, t, start, end):
        SlowDS.calls += 1
        return None


def test_fast_panel_makes_no_outside_call(db):
    SlowDS.calls = 0
    out = asyncio.run(rs.stock_summary(db, SlowDS(), "GOOD", fast=True))
    assert SlowDS.calls == 0 and out["fast"] is True and out["news"] == []


def test_live_panel_does_not_wait_for_a_stuck_source_and_is_cached(db, monkeypatch):
    real_wait = asyncio.wait_for
    monkeypatch.setattr(asyncio, "wait_for", lambda c, timeout: real_wait(c, min(timeout, 0.5)))
    SlowDS.calls = 0
    t0 = time.perf_counter()
    out = asyncio.run(rs.stock_summary(db, SlowDS(), "GOOD"))
    assert time.perf_counter() - t0 < 5  # the stuck analyst source is cut off, not waited for
    assert out["price"] == 12.5 and any(e.startswith("analysts") for e in out["errors"])
    n = SlowDS.calls
    again = asyncio.run(rs.stock_summary(db, SlowDS(), "GOOD"))
    assert again is out and SlowDS.calls == n  # reopened within 10 minutes: no new calls
