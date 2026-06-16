"""Cache-aside helper for a worker, backed by Redis.

Toggleable at runtime so the lab can show the before/after effect of adding
a cache while load is running. Degrades gracefully: if Redis is unreachable,
reads simply fall through to the database.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import redis.asyncio as aioredis


class Cache:
    def __init__(self) -> None:
        self.host = os.environ.get("REDIS_HOST", "redis")
        self.port = int(os.environ.get("REDIS_PORT", "6379"))
        self.ttl = int(os.environ.get("CACHE_TTL", "30"))
        self.enabled = os.environ.get("CACHE_ENABLED", "false").lower() == "true"
        self._client: Optional[aioredis.Redis] = None

    async def connect(self) -> None:
        self._client = aioredis.Redis(
            host=self.host, port=self.port, decode_responses=True,
            socket_connect_timeout=2, socket_timeout=2,
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get(self, key: str) -> Optional[list]:
        if not self.enabled or self._client is None:
            return None
        try:
            raw = await self._client.get(key)
        except Exception:
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def set(self, key: str, value: list) -> None:
        if not self.enabled or self._client is None:
            return
        try:
            await self._client.set(key, json.dumps(value, default=str), ex=self.ttl)
        except Exception:
            pass

    async def flush(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.flushdb()
        except Exception:
            pass
