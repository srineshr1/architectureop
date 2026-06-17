"""Control-plane DB-side optimization toggles.

Currently: the 'missing index' optimization — creates/drops an index on the
products.stock column that the slow-read query filters on. Without it the
query does a sequential scan; with it, an index scan.
"""
from __future__ import annotations

import asyncpg

from .config import settings

INDEX_NAME = "idx_products_stock"
CREATE_INDEX_SQL = f"CREATE INDEX IF NOT EXISTS {INDEX_NAME} ON products (stock)"
DROP_INDEX_SQL = f"DROP INDEX IF EXISTS {INDEX_NAME}"


async def _connect(host: str) -> asyncpg.Connection:
    return await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        host=host,
        port=int(settings.postgres_port),
        database=settings.postgres_db,
        timeout=5.0,
    )


async def set_stock_index(enabled: bool, hosts: list[str]) -> dict:
    """Create or drop the stock index on each given DB host."""
    results = {}
    sql = CREATE_INDEX_SQL if enabled else DROP_INDEX_SQL
    for host in hosts:
        try:
            conn = await _connect(host)
            try:
                await conn.execute(sql)
                results[host] = "ok"
            finally:
                await conn.close()
        except Exception as exc:  # noqa: BLE001
            results[host] = f"error: {exc}"
    return results


async def index_exists(host: str) -> bool:
    try:
        conn = await _connect(host)
        try:
            val = await conn.fetchval(
                "SELECT 1 FROM pg_indexes WHERE indexname = $1", INDEX_NAME
            )
            return val == 1
        finally:
            await conn.close()
    except Exception:
        return False
