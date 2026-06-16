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
    cache_key = f"read:{mode}:{cat}:{limit}"

    # Cache-aside: try cache first, fall back to DB and populate.
    cached = await cache.get(cache_key)
    if cached is not None:
        metrics.record_cache(hit=True)
        return {
            "worker_id": WORKER_ID, "mode": mode, "category": cat,
            "count": len(cached), "rows": cached, "served_from": "cache",
        }

    if mode == "slow":
        rows = await db.slow_read(limit=limit)
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


@app.get("/cache")
async def get_cache():
    return {"worker_id": WORKER_ID, "cache_enabled": cache.enabled}


@app.get("/")
async def root():
    return {"service": "readissue-worker", "worker_id": WORKER_ID}
