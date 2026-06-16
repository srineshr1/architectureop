"""Idempotent Postgres seeder for the ReadIssue lab.

- Waits for Postgres to accept connections.
- Ensures the schema exists (safety net; the init SQL also creates it).
- Seeds up to SEED_ROWS rows using fast COPY, only inserting the shortfall
  so re-running is cheap and idempotent.
"""
from __future__ import annotations

import io
import os
import sys
import time

import psycopg2

from datagen import generate_rows

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS products (
    id          BIGSERIAL PRIMARY KEY,
    sku         TEXT        NOT NULL,
    name        TEXT        NOT NULL,
    category    TEXT        NOT NULL,
    price       NUMERIC(10, 2) NOT NULL,
    stock       INTEGER     NOT NULL,
    description TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_products_category ON products (category);
"""


def connect_with_retry(dsn: str, attempts: int = 30, delay: float = 1.0):
    last_err = None
    for n in range(1, attempts + 1):
        try:
            conn = psycopg2.connect(dsn)
            conn.autocommit = True
            return conn
        except psycopg2.OperationalError as exc:  # pragma: no cover - timing
            last_err = exc
            print(f"[seeder] postgres not ready (attempt {n}/{attempts}): {exc}".strip())
            time.sleep(delay)
    raise SystemExit(f"[seeder] could not connect to postgres: {last_err}")


def current_count(cur) -> int:
    cur.execute("SELECT count(*) FROM products;")
    return int(cur.fetchone()[0])


def copy_rows(cur, start_index: int, count: int) -> None:
    """COPY ``count`` generated rows (offset by start_index) into products."""
    # Generate the full deterministic sequence but only emit the new tail
    # [start_index, start_index + count) so re-seeding stays consistent.
    buf = io.StringIO()
    for i, row in enumerate(generate_rows(start_index + count)):
        if i < start_index:
            continue
        sku, name, category, price, stock, description = row
        # Tab-delimited COPY; escape tabs/newlines defensively.
        fields = [sku, name, category, str(price), str(stock), description]
        clean = ["" if f is None else str(f).replace("\t", " ").replace("\n", " ") for f in fields]
        buf.write("\t".join(clean) + "\n")
    buf.seek(0)
    cur.copy_expert(
        "COPY products (sku, name, category, price, stock, description) FROM STDIN WITH (FORMAT text)",
        buf,
    )


def main() -> int:
    user = os.environ.get("POSTGRES_USER", "readissue")
    password = os.environ.get("POSTGRES_PASSWORD", "readissue")
    host = os.environ.get("POSTGRES_HOST", "postgres")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "readissue")
    target = int(os.environ.get("SEED_ROWS", "50000"))

    dsn = f"host={host} port={port} dbname={db} user={user} password={password}"
    print(f"[seeder] target rows={target} host={host}:{port} db={db}")

    conn = connect_with_retry(dsn)
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        have = current_count(cur)
        print(f"[seeder] existing rows={have}")
        if have >= target:
            print("[seeder] already at/above target; nothing to do.")
            return 0
        to_add = target - have
        print(f"[seeder] inserting {to_add} rows via COPY...")
        t0 = time.time()
        copy_rows(cur, start_index=have, count=to_add)
        dt = time.time() - t0
        final = current_count(cur)
        print(f"[seeder] done in {dt:.2f}s; rows now={final}")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
