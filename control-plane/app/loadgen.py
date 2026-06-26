"""Closed-loop, multi-process load generator.

Fires HTTP read requests at the Traefik ingress at a controllable target RPS
and tracks client-side stats (offered vs completed, errors, latency).

WHY MULTI-PROCESS: a single Python process is GIL-bound to one CPU core and
tops out around a few hundred RPS -- the event loop saturates issuing/parsing
HTTP well before the backend does, so "set 780, get ~300" was a *client*
bottleneck, not the workers. We now fan the load across N worker processes
(one event loop + httpx client each), so the generator scales across cores and
can actually drive the backend into genuine overload. Each child owns its share
``target_rps / N`` and writes its own slot in shared-memory counter arrays
(single writer per slot -> no locking); the parent sums the slots for status.

Includes a traffic-spike scenario that ramps RPS to a peak, holds, then
returns to the previous baseline (implemented in the parent by ramping the
shared target, which every child follows automatically).
"""
from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import threading
import time
from typing import List, Optional, Tuple

import httpx

TICK = 0.1  # scheduling granularity (seconds)
MAX_IN_FLIGHT = 500  # per-child concurrency cap; beyond this we stop offering


def requests_this_tick(rps: float, tick: float, carry: float) -> Tuple[int, float]:
    """How many requests to launch this tick to sustain ``rps``.

    Uses a fractional carry so non-integer (rps*tick) rates stay accurate
    over time. Returns (count, new_carry).
    """
    if rps <= 0:
        return 0, 0.0
    exact = rps * tick + carry
    count = int(exact)
    return count, exact - count


def _default_procs() -> int:
    env = os.environ.get("LOADGEN_PROCS")
    if env:
        try:
            return max(1, int(env))
        except ValueError:
            pass
    return max(1, min(os.cpu_count() or 2, 8))


# --------------------------------------------------------------------------
# Child process: one event loop + one httpx client, driving target_rps / N.
# --------------------------------------------------------------------------
def _child_loop(idx: int, ingress_url: str, ctrl: dict, arr: dict) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = httpx.AsyncClient(
        timeout=10.0,
        limits=httpx.Limits(max_connections=MAX_IN_FLIGHT,
                            max_keepalive_connections=200),
    )

    in_flight = arr["in_flight"]
    sent = arr["sent"]
    completed = arr["completed"]
    errors = arr["errors"]
    lat_sum = arr["lat_sum"]
    lat_count = arr["lat_count"]
    bucket = arr["bucket"]
    actual = arr["actual"]

    async def fire_one(mode: str, limit: int) -> None:
        in_flight[idx] += 1
        t0 = time.perf_counter()
        ok = False
        try:
            r = await client.get(
                f"{ingress_url}/read", params={"mode": mode, "limit": limit}
            )
            ok = r.status_code < 500
        except Exception:
            ok = False
        finally:
            in_flight[idx] -= 1
            completed[idx] += 1
            lat_sum[idx] += (time.perf_counter() - t0) * 1000.0
            lat_count[idx] += 1
            bucket[idx] += 1
            if not ok:
                errors[idx] += 1

    async def main() -> None:
        carry = 0.0
        bucket_start = time.time()
        while ctrl["running"].value:
            nprocs = max(1, ctrl["nprocs"].value)
            share = ctrl["target_rps"].value / nprocs
            mode = "slow" if ctrl["mode"].value else "fast"
            limit = ctrl["limit"].value
            count, carry = requests_this_tick(share, TICK, carry)
            for _ in range(count):
                if in_flight[idx] >= MAX_IN_FLIGHT:
                    break  # back-pressure
                sent[idx] += 1
                loop.create_task(fire_one(mode, limit))
            now = time.time()
            elapsed = now - bucket_start
            if elapsed >= 1.0:
                actual[idx] = bucket[idx] / elapsed
                bucket[idx] = 0
                bucket_start = now
            await asyncio.sleep(TICK)

    try:
        loop.run_until_complete(main())
    finally:
        try:
            loop.run_until_complete(client.aclose())
        finally:
            loop.close()


class LoadGenerator:
    """Parent-side controller fanning load across ``num_procs`` processes.

    Public interface is unchanged: start/stop/set_rps/set_mode/trigger_spike/
    status, so the control-plane endpoints don't need to know it's parallel.
    """

    def __init__(self, ingress_url: str, num_procs: Optional[int] = None):
        self.ingress_url = ingress_url.rstrip("/")
        self.num_procs = int(num_procs) if num_procs else _default_procs()
        self.mode = "fast"  # cached for status display
        self.limit = 10

        # 'fork' lets children inherit the shared-memory objects directly
        # (no pickling) and is the Linux default; the control plane runs on
        # Linux in a container.
        self._ctx = mp.get_context("fork")
        N = self.num_procs

        self._target = self._ctx.Value("d", 0.0, lock=False)
        self._mode = self._ctx.Value("i", 0, lock=False)
        self._limit = self._ctx.Value("i", 10, lock=False)
        self._running = self._ctx.Value("i", 0, lock=False)
        self._nprocs = self._ctx.Value("i", N, lock=False)

        # Per-child counter slots (single writer each -> lock-free).
        self._arr = {
            "sent": self._ctx.Array("q", N, lock=False),
            "completed": self._ctx.Array("q", N, lock=False),
            "errors": self._ctx.Array("q", N, lock=False),
            "in_flight": self._ctx.Array("q", N, lock=False),
            "lat_sum": self._ctx.Array("d", N, lock=False),
            "lat_count": self._ctx.Array("q", N, lock=False),
            "bucket": self._ctx.Array("q", N, lock=False),
            "actual": self._ctx.Array("d", N, lock=False),
        }
        self._ctrl = {
            "target_rps": self._target,
            "mode": self._mode,
            "limit": self._limit,
            "running": self._running,
            "nprocs": self._nprocs,
        }
        self._procs: List[mp.Process] = []

    # --- control (called from the control-plane thread) ---
    def set_rps(self, rps: float) -> None:
        self._target.value = max(0.0, float(rps))

    def set_mode(self, mode: str) -> None:
        if mode in ("fast", "slow"):
            self.mode = mode
            self._mode.value = 1 if mode == "slow" else 0

    def start(self) -> None:
        if self._running.value:
            return
        for a in self._arr.values():
            for i in range(self.num_procs):
                a[i] = 0
        self._running.value = 1
        self._procs = []
        for i in range(self.num_procs):
            p = self._ctx.Process(
                target=_child_loop,
                args=(i, self.ingress_url, self._ctrl, self._arr),
                daemon=True,
            )
            p.start()
            self._procs.append(p)

    def stop(self) -> None:
        self._running.value = 0
        self._target.value = 0.0
        for p in self._procs:
            p.join(timeout=5)
            if p.is_alive():
                p.terminate()
        self._procs = []

    def trigger_spike(self, peak_rps: float, duration_s: float = 10.0,
                      ramp_s: float = 2.0) -> None:
        threading.Thread(
            target=self._spike,
            args=(float(peak_rps), float(duration_s), float(ramp_s)),
            daemon=True,
        ).start()

    def _spike(self, peak: float, duration: float, ramp: float) -> None:
        baseline = self._target.value
        steps = max(1, int(ramp / TICK))
        for s in range(1, steps + 1):
            self._target.value = baseline + (peak - baseline) * (s / steps)
            time.sleep(TICK)
        self._target.value = peak
        time.sleep(duration)
        for s in range(1, steps + 1):
            self._target.value = peak + (baseline - peak) * (s / steps)
            time.sleep(TICK)
        self._target.value = baseline

    # --- status (called from the control-plane thread) ---
    def _sum(self, key: str):
        a = self._arr[key]
        return sum(a[i] for i in range(self.num_procs))

    def status(self) -> dict:
        lat_sum = self._sum("lat_sum")
        lat_count = self._sum("lat_count")
        avg_lat = round(lat_sum / lat_count, 2) if lat_count else 0.0
        return {
            "running": bool(self._running.value),
            "target_rps": round(self._target.value, 1),
            "actual_rps": round(self._sum("actual"), 1),
            "mode": self.mode,
            "in_flight": int(self._sum("in_flight")),
            "sent_total": int(self._sum("sent")),
            "completed_total": int(self._sum("completed")),
            "errors_total": int(self._sum("errors")),
            "avg_latency_ms": avg_lat,
            "procs": self.num_procs,
        }
