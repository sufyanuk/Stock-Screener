"""A provider whose daily quota is used up is not retried (and is skipped for a while)."""
import asyncio
import time

import httpx
import pytest

from app.providers import http as h


def test_daily_quota_429_fails_fast_and_cools_down(monkeypatch):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path == "/quota":
            return httpx.Response(429, json={"Error Message": "Limit Reach . Please upgrade your plan"})
        return httpx.Response(200, json={"ok": True})

    p = h.HttpProvider("fakeq", "http://x", requests_per_second=100)
    p._client = httpx.AsyncClient(base_url="http://x", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(h, "_QUOTA_COOLDOWN", {})
    t0 = time.time()
    with pytest.raises(h.ProviderError) as e:
        asyncio.run(p.get_json("/quota", cache_ttl=0))
    assert e.value.status == 429 and len(calls) == 1 and time.time() - t0 < 1  # no retries
    with pytest.raises(h.ProviderError):
        asyncio.run(p.get_json("/other", cache_ttl=0))
    assert len(calls) == 1  # skipped while cooling down, no request sent


def test_short_rate_limit_429_still_retries(monkeypatch):
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(429, json={"error": "too many requests"}) if n["i"] == 1 else httpx.Response(200, json={"ok": 1})

    async def no_sleep(*_):
        return None

    p = h.HttpProvider("faker", "http://x", requests_per_second=100)
    p._client = httpx.AsyncClient(base_url="http://x", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(h, "_QUOTA_COOLDOWN", {})
    monkeypatch.setattr(h.asyncio, "sleep", no_sleep)
    assert asyncio.run(p.get_json("/r", cache_ttl=0)) == {"ok": 1} and n["i"] == 2
