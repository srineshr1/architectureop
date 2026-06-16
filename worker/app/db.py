"""Async Postgres access for a worker instance.

Uses an asyncpg pool whose size is configurable so we can study connection
pool exhaustion under overload. Read queries intentionally come in two
flavors so later scenarios can flip between a fast indexed read and a
deliberately expensive one.
"""
from __future__ import annotations

import os
from typing import List, Optional

import asyncpg

CATEGORIES = [
    "electronics", "books", "home", "garden", "toys",
    "sports", "grocery", "fashion", "automotive", "beauty",
]


class Database:
    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None
        self.user = os.environ.get("POSTGRES_USER", "readissue")
        self.password = os.environ.get("POSTGRES_PASSWORD", "readissue")
        self.host = os.environ.get("POSTGRES_HOST", "postgres")
        self.port = int(os.environ.get("POSTGRES_PORT", "5432"))
        self.db = os.environ.get("POSTGRES_DB", "readissue")
        self.pool_size = int(os.environ.get("DB_POOL_SIZE", "10"))

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(
            user=self.user,
            password=self.password,
            host=self.host,
            port=self.port,
            database=self.db,
            min_size=1,
            max_size=self.pool_size,
            command_timeout=60,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def ready(self) -> bool:
        return self._pool is not None

    async def fast_read(self, category: str, limit: int = 20) -> List[dict]:
        """Indexed read by category."""
        assert self._pool is not None
        rows = await self._pool.fetch(
            """
            SELECT id, sku, name, category, price, stock
            FROM products
            WHERE category = $1
            ORDER BY id
            LIMIT $2
            """,
            category,
            limit,
        )
        return [dict(r) for r in rows]

    async def slow_read(self, limit: int = 20) -> List[dict]:
        """Deliberately expensive read used by the 'slow query' scenario.

        Forces a full scan + sort on an unindexed expression so the DB has to
        do real work, which drives CPU and latency up under load.
        """
        assert self._pool is not None
        rows = await self._pool.fetch(
            """
            SELECT id, sku, name, category, price, stock
            FROM products
            WHERE lower(description) LIKE '%item number ' || (id % 7)::text || '%'
            ORDER BY md5(name || price::text)
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]
