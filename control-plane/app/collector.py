"""Live metrics collection for the lab.

Polls, every interval:
  * Docker per-container stats -> CPU% and memory for each worker
  * each worker's /metrics endpoint -> request counts + latency
  * Postgres pg_stat_activity -> active connections

Derives per-worker and system-wide RPS from request-count deltas, and
broadcasts consolidated snapshots to subscribers (the WebSocket hub).
"""
from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Optional

import asyncpg
import httpx

from .config import settings
from .orchestrator import Orchestrator


# ----------------------------- pure helpers -----------------------------
def compute_cpu_percent(stats: dict) -> float:
    """Standard Docker CPU% from a stats sample (cpu_stats vs precpu_stats)."""
    try:
        cpu = stats["cpu_stats"]
        pre = stats["precpu_stats"]
        cpu_delta = cpu["cpu_usage"]["total_usage"] - pre["cpu_usage"]["total_usage"]
        system_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
        online = cpu.get("online_cpus") or len(
            cpu["cpu_usage"].get("percpu_usage") or [1]
        )
        if system_delta > 0 and cpu_delta >= 0:
            return round((cpu_delta / system_delta) * online * 100.0, 2)
    except (KeyError, TypeError, ZeroDivisionError):
        pass
    return 0.0


def compute_mem_mb(stats: dict) -> float:
    try:
        usage = stats["memory_stats"]["usage"]
        # subtract page cache if present for a truer working-set figure
        cache = stats["memory_stats"].get("stats", {}).get("inactive_file", 0)
        return round(max(0, usage - cache) / (1024 * 1024), 1)
    except (KeyError, TypeError):
        return 0.0


def derive_rps(curr_total: int, prev_total: Optional[int], dt: float) -> float:
    if prev_total is None or dt <= 0:
        return 0.0
    delta = curr_total - prev_total
    if delta < 0:  # worker restarted / counter reset
        return 0.0
    return round(delta / dt, 1)


# ----------------------------- collector -----------------------------
class MetricsCollector:
    def __init__(self, orchestrator: Orchestrator, interval: float = 1.0,
                 load_status_fn=None, cache_status_fn=None, autoscale_status_fn=None):
        self.orch = orchestrator
        self.interval = interval
        self.load_status_fn = load_status_fn
        self.cache_status_fn = cache_status_fn
        self.autoscale_status_fn = autoscale_status_fn
        self.latest: dict = {"ts": 0, "instances": [], "system": {}, "db": {},
                             "load": {}, "cache": {}, "autoscale": {}}
        self._prev: Dict[str, tuple] = {}  # worker_id -> (requests_total, ts)
        self._subscribers: set[asyncio.Queue] = set()
        self._task: Optional[asyncio.Task] = None
        self._running = False

    # --- subscription (used by the WebSocket hub) ---
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=10)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def _broadcast(self, snapshot: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(snapshot)
            except asyncio.QueueFull:
                # slow consumer: drop oldest then enqueue latest
                try:
                    q.get_nowait()
                    q.put_nowait(snapshot)
                except Exception:
                    pass

    # --- lifecycle ---
    def start(self) -> None:
        if not self._running:
            self._running = True
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self._running:
            try:
                self.latest = await self.gather()
                self._broadcast(self.latest)
            except Exception as exc:  # keep the loop alive
                self.latest = {**self.latest, "error": str(exc)}
            await asyncio.sleep(self.interval)

    # --- gathering ---
    def _docker_stats(self, container_id: str) -> dict:
        c = self.orch._client.containers.get(container_id)
        return c.stats(stream=False)

    async def _worker_metrics(self, client: httpx.AsyncClient, ip: str) -> dict:
        r = await client.get(f"http://{ip}:8000/metrics", timeout=2.0)
        return r.json()

    async def _gather_instance(self, client: httpx.AsyncClient, inst: dict, now: float) -> dict:
        wid = inst["worker_id"]
        ip = inst.get("ip")
        cpu = mem = 0.0
        wm: dict = {}
        if ip:
            try:
                stats = await asyncio.to_thread(self._docker_stats, inst["container_id"])
                cpu = compute_cpu_percent(stats)
                mem = compute_mem_mb(stats)
            except Exception:
                pass
            try:
                wm = await self._worker_metrics(client, ip)
            except Exception:
                wm = {}
        req_total = int(wm.get("requests_total", 0))
        prev = self._prev.get(wid)
        prev_total = prev[0] if prev else None
        prev_ts = prev[1] if prev else now
        rps = derive_rps(req_total, prev_total, now - prev_ts)
        self._prev[wid] = (req_total, now)
        return {
            "worker_id": wid,
            "status": inst.get("status"),
            "health": inst.get("health"),
            "cpu_pct": cpu,
            "mem_mb": mem,
            "rps": rps,
            "requests_total": req_total,
            "in_flight": int(wm.get("in_flight", 0)),
            "errors_total": int(wm.get("errors_total", 0)),
            "latency_p50_ms": wm.get("latency_p50_ms", 0.0),
            "latency_p95_ms": wm.get("latency_p95_ms", 0.0),
            "cache_hit_ratio": wm.get("cache_hit_ratio", 0.0),
        }

    async def _pg_stats(self) -> dict:
        try:
            conn = await asyncpg.connect(
                user=settings.postgres_user,
                password=settings.postgres_password,
                host=settings.collector_pg_host,
                port=int(settings.postgres_port),
                database=settings.postgres_db,
                timeout=2.0,
            )
            try:
                total = await conn.fetchval(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = $1",
                    settings.postgres_db,
                )
                active = await conn.fetchval(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = $1 AND state = 'active'",
                    settings.postgres_db,
                )
                return {"connections": int(total), "active_queries": int(active)}
            finally:
                await conn.close()
        except Exception:
            return {"connections": None, "active_queries": None}

    async def gather(self) -> dict:
        now = time.time()
        instances = await asyncio.to_thread(self.orch.list_instances)
        running = [i for i in instances if i.get("status") == "running"]

        async with httpx.AsyncClient() as client:
            per_instance: List[dict] = await asyncio.gather(
                *[self._gather_instance(client, i, now) for i in running]
            ) if running else []

        # prune prev-state for instances that are gone
        live_ids = {i["worker_id"] for i in running}
        self._prev = {k: v for k, v in self._prev.items() if k in live_ids}

        pg = await self._pg_stats()

        load = {}
        if self.load_status_fn is not None:
            try:
                load = self.load_status_fn()
            except Exception:
                load = {}

        cache = {}
        if self.cache_status_fn is not None:
            try:
                cache = self.cache_status_fn()
            except Exception:
                cache = {}

        autoscale = {}
        if self.autoscale_status_fn is not None:
            try:
                autoscale = self.autoscale_status_fn()
            except Exception:
                autoscale = {}

        n = len(per_instance)
        total_rps = round(sum(i["rps"] for i in per_instance), 1)
        avg_cpu = round(sum(i["cpu_pct"] for i in per_instance) / n, 2) if n else 0.0
        max_p95 = max((i["latency_p95_ms"] for i in per_instance), default=0.0)
        total_inflight = sum(i["in_flight"] for i in per_instance)
        total_errors = sum(i["errors_total"] for i in per_instance)

        return {
            "ts": round(now, 3),
            "instances": per_instance,
            "system": {
                "instance_count": n,
                "total_rps": total_rps,
                "avg_cpu_pct": avg_cpu,
                "max_latency_p95_ms": max_p95,
                "total_in_flight": total_inflight,
                "total_errors": total_errors,
            },
            "db": pg,
            "load": load,
            "cache": cache,
            "autoscale": autoscale,
        }
