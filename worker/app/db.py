"""Async Postgres access for a worker instance.

Holds a primary connection pool and, optionally, one pool per read replica.
Reads can be routed to the primary or round-robined across replicas at
runtime (the read-replica optimization). Pool size is configurable so we can
also study connection-pool exhaustion.
"""
from __future__ import annotations

import itertools
import os
import random
from typing import List, Optional

import asyncpg

CATEGORIES = [
    "electronics", "books", "home", "garden", "toys",
    "sports", "grocery", "fashion", "automotive", "beauty",
]


class Database:
    def __init__(self) -> None:
        self._pool: Optional[asyncpg.Pool] = None
        self._replica_pools: List[asyncpg.Pool] = []
        self._rr = itertools.cycle([0])  # replaced once replicas connect
        self.user = os.environ.get("POSTGRES_USER", "readissue")
        self.password = os.environ.get("POSTGRES_PASSWORD", "readissue")
        self.host = os.environ.get("POSTGRES_HOST", "postgres")
        self.port = int(os.environ.get("POSTGRES_PORT", "5432"))
        self.db = os.environ.get("POSTGRES_DB", "readissue")
        self.pool_size = int(os.environ.get("DB_POOL_SIZE", "10"))
        # Comma-separated replica hostnames (each a full read-only Postgres).
        self.replica_hosts = [
            h.strip() for h in os.environ.get("REPLICA_HOSTS", "").split(",") if h.strip()
        ]
        self.use_replicas = os.environ.get("REPLICAS_ENABLED", "false").lower() == "true"

    async def _make_pool(self, host: str, port: int) -> asyncpg.Pool:
        stmt_cache = 0 if port == 6432 else 100  # PgBouncer txn mode needs 0
        return await asyncpg.create_pool(
            user=self.user, password=self.password, host=host, port=port,
            database=self.db, min_size=1, max_size=self.pool_size,
            command_timeout=60, statement_cache_size=stmt_cache,
        )

    async def connect(self) -> None:
        self._pool = await self._make_pool(self.host, self.port)
        # Pre-connect replica pools (always, so the toggle is instant). Replicas
        # are independent read-only Postgres instances on port 5432.
        self._replica_pools = []
        for rh in self.replica_hosts:
            try:
                self._replica_pools.append(await self._make_pool(rh, 5432))
            except Exception:
                pass  # replica not up; degrade to primary
        if self._replica_pools:
            self._rr = itertools.cycle(range(len(self._replica_pools)))

    async def close(self) -> None:
        for p in [self._pool, *self._replica_pools]:
            if p is not None:
                await p.close()
        self._pool = None
        self._replica_pools = []

    @property
    def ready(self) -> bool:
        return self._pool is not None

    def set_use_replicas(self, enabled: bool) -> None:
        self.use_replicas = enabled

    def _read_pool(self) -> asyncpg.Pool:
        """Pick the pool to read from: round-robin replica when enabled."""
        if self.use_replicas and self._replica_pools:
            return self._replica_pools[next(self._rr)]
        return self._pool

    async def fast_read(self, category: str, limit: int = 20) -> List[dict]:
        """Indexed read by category."""
        pool = self._read_pool()
        rows = await pool.fetch(
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
        """Read that filters on the (by default un-indexed) ``stock`` column.

        Without an index this forces a sequential scan over the whole table on
        every request, which becomes a real CPU/IO bottleneck under concurrency.
        Adding an index on ``stock`` turns it into a fast index scan.
        """
        pool = self._read_pool()
        stock = random.randint(0, 1000)
        rows = await pool.fetch(
            """
            SELECT id, sku, name, category, price, stock
            FROM products
            WHERE stock = $1
            ORDER BY price DESC
            LIMIT $2
            """,
            stock,
            limit,
        )
        return [dict(r) for r in rows]
