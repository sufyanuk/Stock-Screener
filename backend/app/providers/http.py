"""Shared HTTP client: retries with exponential backoff + jitter, per-provider token-bucket rate
limiting, short-lived response caching, and structured errors. All provider adapters use it."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)


class ProviderError(Exception):
    def __init__(self, provider: str, message: str, status: int | None = None):
        super().__init__(f"[{provider}] {message}")
        self.provider, self.status = provider, status


class NotConfigured(ProviderError):
    pass


class RateLimiter:
    def __init__(self, per_second: float):
        self.rate = per_second
        self.tokens = per_second
        self.updated = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.rate, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


class TTLCache:
    """In-process cache. Swap for Redis via ``RedisCache`` in services/cache.py in production."""

    def __init__(self):
        self._d: dict[str, tuple[float, object]] = {}

    def get(self, k: str):
        v = self._d.get(k)
        if v and v[0] > time.time():
            return v[1]
        self._d.pop(k, None)
        return None

    def set(self, k: str, v: object, ttl: int) -> None:
        self._d[k] = (time.time() + ttl, v)


@dataclass
class HttpProvider:
    name: str
    base_url: str
    requests_per_second: float = 5.0
    max_retries: int = 4
    timeout: float = 30.0
    headers: dict | None = None
    cache_ttl: int = 300

    def __post_init__(self):
        self._limiter = RateLimiter(self.requests_per_second)
        self._cache = TTLCache()
        self._client: httpx.AsyncClient | None = None

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout, headers=self.headers or {})
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_json(self, path: str, params: dict | None = None, cache_ttl: int | None = None,
                       redact: tuple[str, ...] = ("apiKey", "apikey", "token")) -> object:
        params = params or {}
        key = hashlib.sha256((path + json.dumps(params, sort_keys=True)).encode()).hexdigest()
        ttl = self.cache_ttl if cache_ttl is None else cache_ttl
        if ttl and (hit := self._cache.get(key)) is not None:
            return hit
        safe = {k: ("***" if k in redact else v) for k, v in params.items()}
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            await self._limiter.acquire()
            try:
                r = await self.client().get(path, params=params)
            except httpx.TransportError as e:
                err: ProviderError = ProviderError(self.name, f"transport error {e!r} on {path}")
            else:
                if r.status_code == 200:
                    data = r.json()
                    if ttl:
                        self._cache.set(key, data, ttl)
                    return data
                if r.status_code in (401, 403):
                    raise ProviderError(self.name, f"unauthorized ({r.status_code}) on {path}", r.status_code)
                if r.status_code == 404:
                    raise ProviderError(self.name, f"not found on {path} {safe}", 404)
                err = ProviderError(self.name, f"HTTP {r.status_code} on {path} {safe}", r.status_code)
                if r.status_code not in (429, 500, 502, 503, 504):
                    raise err
                if ra := r.headers.get("Retry-After"):
                    try:
                        delay = max(delay, float(ra))
                    except ValueError:
                        pass
            if attempt == self.max_retries:
                raise err
            log.warning("%s; retry %s in %.1fs", err, attempt + 1, delay)
            await asyncio.sleep(delay + random.random() * 0.25 * delay)
            delay *= 2
        raise AssertionError("unreachable")

    async def get_text(self, url_or_path: str) -> str:
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            await self._limiter.acquire()
            try:
                r = await self.client().get(url_or_path)
                if r.status_code == 200:
                    return r.text
                if r.status_code not in (429, 500, 502, 503, 504):
                    raise ProviderError(self.name, f"HTTP {r.status_code} on {url_or_path}", r.status_code)
            except httpx.TransportError as e:
                if attempt == self.max_retries:
                    raise ProviderError(self.name, f"transport error {e!r}") from e
            if attempt == self.max_retries:
                raise ProviderError(self.name, f"retries exhausted on {url_or_path}")
            await asyncio.sleep(delay)
            delay *= 2
        raise AssertionError("unreachable")
