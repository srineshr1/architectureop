"""ReadIssue control plane.

The brain of the lab: orchestrates worker instances, collects live metrics,
(later) drives load and runs scenarios, and serves the dashboard's API +
WebSocket.

This revision adds the metrics collector + live WebSocket stream (Task 5).
"""
from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .autoscaler import AutoScaler
from .collector import MetricsCollector
from .config import settings
from .loadgen import LoadGenerator
from . import optimizations
from .orchestrator import Orchestrator, OrchestratorError

app = FastAPI(title="ReadIssue Control Plane", version="1.0")

# Local-only dashboard; permissive CORS is fine for a localhost lab.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Desired cache state; applied to existing workers via fan-out and to new
# workers via their startup env.
cache_state = {"enabled": False}

# Read-path optimization toggles surfaced on the dashboard.
optimization_state = {
    "index": False,       # index on products.stock
    "pgbouncer": False,   # workers routed through PgBouncer
    "replicas": False,    # reads spread across read replicas
    "rate_limit": False,  # worker load shedding
}


def _spawn_worker():
    return orchestrator.create_instance(extra_env=_worker_extra_env())


def _worker_extra_env() -> dict:
    """Env passed to newly-spawned workers reflecting current toggles."""
    env = {"CACHE_ENABLED": "true" if cache_state["enabled"] else "false"}
    if optimization_state["pgbouncer"]:
        env["POSTGRES_HOST"] = "pgbouncer"
        env["POSTGRES_PORT"] = "6432"
    return env


orchestrator = Orchestrator()
loadgen = LoadGenerator(settings.ingress_url)
autoscaler = AutoScaler(
    get_metrics=lambda: collector.latest,
    scale_up=_spawn_worker,
    scale_down=orchestrator.destroy_one,
    max_workers=settings.max_workers,
)
collector = MetricsCollector(
    orchestrator,
    load_status_fn=loadgen.status,
    cache_status_fn=lambda: dict(cache_state),
    autoscale_status_fn=lambda: autoscaler.status(),
    optimization_status_fn=lambda: dict(optimization_state),
)


@app.on_event("startup")
async def _startup() -> None:
    collector.start()
    loadgen.start()
    autoscaler.start()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await autoscaler.stop()
    loadgen.stop()
    await collector.stop()


# ----------------------------- health/config -----------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "max_workers": settings.max_workers}


# ----------------------------- instances -----------------------------
@app.get("/api/instances")
def list_instances():
    return {"instances": orchestrator.list_instances()}


@app.post("/api/instances")
def create_instance():
    try:
        info = _spawn_worker()
    except OrchestratorError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return info


@app.delete("/api/instances/{worker_id}")
def delete_instance(worker_id: str):
    ok = orchestrator.destroy_instance(worker_id)
    if not ok:
        raise HTTPException(status_code=404, detail="instance not found")
    return {"deleted": worker_id}


@app.delete("/api/instances")
def delete_all_instances():
    n = orchestrator.destroy_all()
    return {"deleted_count": n}


# ----------------------------- metrics -----------------------------
@app.get("/api/metrics")
def latest_metrics():
    """Most recent collected snapshot (REST convenience)."""
    return collector.latest


# ----------------------------- load generator -----------------------------
@app.get("/api/load")
def load_status():
    return loadgen.status()


@app.post("/api/load")
def set_load(payload: dict):
    if "target_rps" in payload:
        loadgen.set_rps(payload["target_rps"])
    if "mode" in payload:
        loadgen.set_mode(payload["mode"])
    return loadgen.status()


@app.post("/api/load/spike")
async def load_spike(payload: dict | None = None):
    payload = payload or {}
    peak = float(payload.get("peak_rps", 500))
    duration = float(payload.get("duration_s", 10))
    ramp = float(payload.get("ramp_s", 2))
    loadgen.trigger_spike(peak, duration, ramp)
    return {"spike": {"peak_rps": peak, "duration_s": duration, "ramp_s": ramp}}


@app.post("/api/load/stop")
def load_stop():
    loadgen.set_rps(0)
    return loadgen.status()


# ----------------------------- cache -----------------------------
@app.get("/api/cache")
def get_cache():
    return dict(cache_state)


@app.post("/api/cache")
async def set_cache(payload: dict):
    enabled = bool(payload.get("enabled", cache_state["enabled"]))
    flush = bool(payload.get("flush", False))
    cache_state["enabled"] = enabled

    # Fan out to all running workers so the toggle takes effect live.
    instances = orchestrator.list_instances()
    results = []
    async with httpx.AsyncClient(timeout=3.0) as client:
        for inst in instances:
            ip = inst.get("ip")
            if not ip:
                continue
            try:
                r = await client.post(
                    f"http://{ip}:8000/cache",
                    json={"enabled": enabled, "flush": flush},
                )
                results.append({inst["worker_id"]: r.json().get("cache_enabled")})
            except Exception as exc:  # noqa: BLE001
                results.append({inst["worker_id"]: f"error: {exc}"})
    return {"enabled": enabled, "flushed": flush, "workers": results}


# ----------------------------- autoscaling -----------------------------
@app.get("/api/autoscale")
def get_autoscale():
    return autoscaler.status()


@app.post("/api/autoscale")
def set_autoscale(payload: dict):
    autoscaler.update_config(payload)
    return autoscaler.status()


# ----------------------------- scenarios -----------------------------
@app.post("/api/scenario/slow")
def scenario_slow(payload: dict | None = None):
    """Slow/heavy query storm: switch load to expensive queries."""
    payload = payload or {}
    rps = float(payload.get("rps", 80))
    loadgen.set_mode("slow")
    loadgen.set_rps(rps)
    return {"scenario": "slow_queries", "rps": rps, "mode": "slow"}


@app.post("/api/scenario/crash")
def scenario_crash():
    """Hard-kill a random instance to observe LB failover + recovery."""
    wid = orchestrator.kill_random()
    if wid is None:
        raise HTTPException(status_code=404, detail="no running instances to crash")
    return {"scenario": "instance_crash", "killed": wid}


@app.post("/api/scenario/stampede")
async def scenario_stampede(payload: dict | None = None):
    """Cache stampede: flush all caches, then burst load so every request
    misses at once and slams the database."""
    payload = payload or {}
    peak = float(payload.get("peak_rps", 800))
    duration = float(payload.get("duration_s", 10))

    # Ensure caching is on, then flush every worker's cache simultaneously.
    cache_state["enabled"] = True
    instances = orchestrator.list_instances()
    flushed = []
    async with httpx.AsyncClient(timeout=3.0) as client:
        for inst in instances:
            ip = inst.get("ip")
            if not ip:
                continue
            try:
                await client.post(
                    f"http://{ip}:8000/cache", json={"enabled": True, "flush": True}
                )
                flushed.append(inst["worker_id"])
            except Exception:
                pass
    # Burst traffic into the now-cold cache.
    loadgen.set_mode("slow")
    loadgen.trigger_spike(peak, duration, ramp_s=1.0)
    return {"scenario": "cache_stampede", "flushed": flushed,
            "peak_rps": peak, "duration_s": duration}


# ----------------------------- optimizations -----------------------------
def _db_hosts() -> list[str]:
    """DB hosts the control plane manages DDL on (primary + replicas)."""
    hosts = [settings.collector_pg_host]
    hosts += [h.strip() for h in settings.replica_hosts.split(",") if h.strip()]
    return hosts


@app.get("/api/optimizations")
def get_optimizations():
    return dict(optimization_state)


@app.post("/api/optimizations/index")
async def set_index(payload: dict):
    enabled = bool(payload.get("enabled", not optimization_state["index"]))
    results = await optimizations.set_stock_index(enabled, _db_hosts())
    optimization_state["index"] = enabled
    return {"index": enabled, "hosts": results}


def _recreate_workers() -> int:
    """Recreate all workers so they pick up current routing env (DB host etc)."""
    n = orchestrator.count_instances()
    orchestrator.destroy_all()
    for _ in range(n):
        try:
            orchestrator.create_instance(extra_env=_worker_extra_env())
        except OrchestratorError:
            break
    return n


@app.post("/api/optimizations/pgbouncer")
def set_pgbouncer(payload: dict):
    enabled = bool(payload.get("enabled", not optimization_state["pgbouncer"]))
    optimization_state["pgbouncer"] = enabled
    recreated = _recreate_workers()
    return {"pgbouncer": enabled, "recreated_workers": recreated}


@app.post("/api/optimizations/replicas")
async def set_replicas(payload: dict):
    enabled = bool(payload.get("enabled", not optimization_state["replicas"]))
    optimization_state["replicas"] = enabled
    # Fan out to workers: flip read routing (pools are pre-connected, instant).
    instances = orchestrator.list_instances()
    results = []
    async with httpx.AsyncClient(timeout=3.0) as client:
        for inst in instances:
            ip = inst.get("ip")
            if not ip:
                continue
            try:
                r = await client.post(f"http://{ip}:8000/db", json={"replicas": enabled})
                results.append({inst["worker_id"]: r.json().get("use_replicas")})
            except Exception as exc:  # noqa: BLE001
                results.append({inst["worker_id"]: f"error: {exc}"})
    return {"replicas": enabled, "workers": results}


@app.post("/api/optimizations/rate_limit")
async def set_rate_limit(payload: dict):
    enabled = bool(payload.get("enabled", not optimization_state["rate_limit"]))
    max_inflight = int(payload.get("max_in_flight", 50) or 50)
    optimization_state["rate_limit"] = enabled
    instances = orchestrator.list_instances()
    results = []
    async with httpx.AsyncClient(timeout=3.0) as client:
        for inst in instances:
            ip = inst.get("ip")
            if not ip:
                continue
            try:
                r = await client.post(
                    f"http://{ip}:8000/shed",
                    json={"enabled": enabled, "max_inflight": max_inflight},
                )
                results.append({inst["worker_id"]: r.json().get("enabled")})
            except Exception as exc:  # noqa: BLE001
                results.append({inst["worker_id"]: f"error: {exc}"})
    return {"rate_limit": enabled, "max_in_flight": max_inflight, "workers": results}


@app.websocket("/ws/metrics")
async def ws_metrics(ws: WebSocket):
    await ws.accept()
    q = collector.subscribe()
    try:
        # send the current snapshot immediately so new clients aren't blank
        await ws.send_json(collector.latest)
        while True:
            snapshot = await q.get()
            await ws.send_json(snapshot)
    except WebSocketDisconnect:
        pass
    finally:
        collector.unsubscribe(q)
