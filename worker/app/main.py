"""ReadIssue worker instance: a containerized read API.

One of these runs per "instance" in the lab. The control plane spawns many
of them behind Traefik. Each tracks its own metrics and reads the products
table from Postgres.
"""
from __future__ import annotations

import os
import random
import socket
import time

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from .cache import Cache
from .db import CATEGORIES, Database
from .metrics import Metrics

WORKER_ID = os.environ.get("WORKER_ID") or socket.gethostname()

app = FastAPI(title="ReadIssue Worker", version="1.0")
db = Database()
cache = Cache()
metrics = Metrics(WORKER_ID)

# Load-shedding / admission control state.
rate_limit = {
    "enabled": os.environ.get("RATE_LIMIT_ENABLED", "false").lower() == "true",
    "max_inflight": int(os.environ.get("MAX_INFLIGHT", "50")),
}

# Skewed (hot-key) access pattern for the slow read. Real read traffic follows
# a power law -- a small set of popular items gets most of the requests -- so a
# modest cache of the hot set absorbs most load. HOT_KEY_RATIO of requests hit
# the hot set of HOT_KEYS values; the rest spread across the long tail. This is
# both more realistic than uniform random AND what makes a cache visibly work.
STOCK_MAX = 1000
HOT_KEYS = int(os.environ.get("HOT_KEYS", "50"))
HOT_KEY_RATIO = float(os.environ.get("HOT_KEY_RATIO", "0.9"))


def _pick_stock() -> int:
    if random.random() < HOT_KEY_RATIO:
        return random.randint(0, max(0, HOT_KEYS - 1))
    return random.randint(0, STOCK_MAX)


@app.on_event("startup")
async def _startup() -> None:
    await db.connect()
    await cache.connect()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await db.close()
    await cache.close()


@app.middleware("http")
async def _track(request: Request, call_next):
    # Don't count introspection endpoints as user traffic.
    if request.url.path in ("/metrics", "/health"):
        return await call_next(request)

    # Admission control: if shedding is on and we're already at the concurrency
    # limit, reject fast with 429 instead of queueing (protects accepted-request
    # latency). in_flight is the count of requests currently being processed.
    if rate_limit["enabled"] and metrics.in_flight >= rate_limit["max_inflight"]:
        metrics.record_shed()
        return JSONResponse(
            {"error": "overloaded", "worker_id": WORKER_ID}, status_code=429
        )

    metrics.begin()
    t0 = time.perf_counter()
    error = False
    try:
        response = await call_next(request)
        if response.status_code >= 500:
            error = True
        return response
    except Exception:
        error = True
        raise
    finally:
        metrics.end((time.perf_counter() - t0) * 1000.0, error=error)


@app.get("/health")
async def health():
    return {"status": "ok" if db.ready else "starting", "worker_id": WORKER_ID}


@app.get("/metrics")
async def get_metrics():
    return metrics.snapshot()


@app.get("/read")
async def read(
    mode: str = Query("fast", pattern="^(fast|slow)$"),
    category: str | None = None,
    limit: int = Query(20, ge=1, le=200),
):
    if not db.ready:
        return JSONResponse({"error": "db not ready"}, status_code=503)
    cat = category or random.choice(CATEGORIES)
    # Build the cache key from the value the query ACTUALLY varies on, so a hit
    # returns the correct rows: category for fast reads, the stock filter for
    # slow reads (a bounded 0..1000 key space -> a small cache covers it all).
    if mode == "slow":
        stock = _pick_stock()
        cache_key = f"read:slow:{stock}:{limit}"
    else:
        cache_key = f"read:fast:{cat}:{limit}"

    # Cache-aside: try cache first, fall back to DB and populate.
    cached = await cache.get(cache_key)
    if cached is not None:
        metrics.record_cache(hit=True)
        return {
            "worker_id": WORKER_ID, "mode": mode, "category": cat,
            "count": len(cached), "rows": cached, "served_from": "cache",
        }

    if mode == "slow":
        rows = await db.slow_read(limit=limit, stock=stock)
    else:
        rows = await db.fast_read(cat, limit=limit)

    if cache.enabled:
        metrics.record_cache(hit=False)
        await cache.set(cache_key, rows)

    return {
        "worker_id": WORKER_ID,
        "mode": mode,
        "category": cat,
        "count": len(rows),
        "rows": rows,
        "served_from": "db",
    }


@app.post("/cache")
async def set_cache(payload: dict):
    if "enabled" in payload:
        cache.enabled = bool(payload["enabled"])
    if payload.get("flush"):
        await cache.flush()
    return {"worker_id": WORKER_ID, "cache_enabled": cache.enabled}


@app.post("/db")
async def set_db(payload: dict):
    """Toggle replica read-routing at runtime (read-replica optimization)."""
    if "replicas" in payload:
        db.set_use_replicas(bool(payload["replicas"]))
    return {
        "worker_id": WORKER_ID,
        "use_replicas": db.use_replicas,
        "replica_count": len(db._replica_pools),
    }


@app.post("/shed")
async def set_shed(payload: dict):
    """Toggle load shedding / set the in-flight concurrency cap."""
    if "enabled" in payload:
        rate_limit["enabled"] = bool(payload["enabled"])
    if payload.get("max_inflight"):
        rate_limit["max_inflight"] = int(payload["max_inflight"])
    return {"worker_id": WORKER_ID, **rate_limit}


@app.get("/cache")
async def get_cache():
    return {"worker_id": WORKER_ID, "cache_enabled": cache.enabled}


@app.get("/")
async def root():
    return {"service": "readissue-worker", "worker_id": WORKER_ID}
