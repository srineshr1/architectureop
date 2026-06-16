"""Unit tests for the worker Cache (no real Redis)."""
import asyncio
import importlib.util
import json
from pathlib import Path

# Load worker/app/cache.py directly (avoid the 'app' package name collision).
_CACHE_PATH = Path(__file__).resolve().parents[1] / "worker" / "app" / "cache.py"
_spec = importlib.util.spec_from_file_location("worker_cache_mod", _CACHE_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
Cache = _mod.Cache


class FakeRedis:
    def __init__(self):
        self.store = {}
    async def get(self, k):
        return self.store.get(k)
    async def set(self, k, v, ex=None):
        self.store[k] = v
    async def flushdb(self):
        self.store.clear()
    async def aclose(self):
        pass


def _cache(enabled):
    c = Cache()
    c.enabled = enabled
    c._client = FakeRedis()
    return c


def test_disabled_cache_returns_none_and_skips_set():
    c = _cache(False)
    async def run():
        assert await c.get("k") is None
        await c.set("k", [{"a": 1}])
        # nothing stored because disabled
        assert c._client.store == {}
    asyncio.run(run())


def test_enabled_cache_set_then_get_roundtrip():
    c = _cache(True)
    async def run():
        rows = [{"id": 1, "name": "x"}]
        await c.set("read:fast:books:10", rows)
        got = await c.get("read:fast:books:10")
        assert got == rows
    asyncio.run(run())


def test_get_miss_returns_none():
    c = _cache(True)
    async def run():
        assert await c.get("missing") is None
    asyncio.run(run())


def test_flush_clears():
    c = _cache(True)
    async def run():
        await c.set("k", [1, 2])
        await c.flush()
        assert await c.get("k") is None
    asyncio.run(run())


def test_set_serializes_with_default_str():
    # Decimals/dates serialized via default=str shouldn't raise.
    import datetime
    c = _cache(True)
    async def run():
        await c.set("k", [{"when": datetime.date(2020, 1, 1)}])
        raw = c._client.store["k"]
        assert json.loads(raw)[0]["when"] == "2020-01-01"
    asyncio.run(run())
