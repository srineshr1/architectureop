"""Closed-loop load generator.

Fires HTTP read requests at the Traefik ingress at a controllable target RPS
and tracks client-side stats (offered vs completed, errors, latency).

It runs on its OWN dedicated event-loop thread, isolated from the control
plane's loop (which serves the API + metrics collector). Sharing a single
event loop caused the generator to self-congest at high concurrency --
requests piled up and latency exploded even though the backend was idle,
producing misleading numbers. A dedicated loop keeps generation and
measurement honest.

Includes a traffic-spike scenario that ramps RPS to a peak, holds, then
returns to the previous baseline.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Optional, Tuple

import httpx

TICK = 0.1  # scheduling granularity (seconds)
MAX_IN_FLIGHT = 500  # concurrency cap; beyond this we stop offering (back-pressure)


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


class LoadGenerator:
    def __init__(self, ingress_url: str):
        self.ingress_url = ingress_url.rstrip("/")
        self.target_rps = 0.0
        self.mode = "fast"
        self.limit = 10

        self._running = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._sem: Optional[asyncio.Semaphore] = None
        self._spike_task: Optional[asyncio.Task] = None

        # stats (plain attrs; reads are atomic enough in CPython for a dashboard)
        self.sent_total = 0
        self.completed_total = 0
        self.errors_total = 0
        self.in_flight = 0
        self._lat_sum = 0.0
        self._lat_count = 0
        self.actual_rps = 0.0
        self._bucket = 0
        self._bucket_start = time.time()

    # --- control (called from the control-plane thread) ---
    def set_rps(self, rps: float) -> None:
        self.target_rps = max(0.0, float(rps))

    def set_mode(self, mode: str) -> None:
        if mode in ("fast", "slow"):
            self.mode = mode

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_thread, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self.target_rps = 0.0
        if self._thread:
            self._thread.join(timeout=5)

    def trigger_spike(self, peak_rps: float, duration_s: float = 10.0,
                      ramp_s: float = 2.0) -> None:
        """Schedule the spike coroutine onto the generator's own loop."""
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            self._spike(peak_rps, duration_s, ramp_s), self._loop
        )

    # --- dedicated thread / event loop ---
    def _run_thread(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._client = httpx.AsyncClient(
            timeout=10.0,
            limits=httpx.Limits(max_connections=MAX_IN_FLIGHT,
                                max_keepalive_connections=200),
        )
        self._sem = asyncio.Semaphore(MAX_IN_FLIGHT)
        try:
            self._loop.run_until_complete(self._loop_main())
        finally:
            self._loop.run_until_complete(self._client.aclose())
            self._loop.close()

    async def _spike(self, peak: float, duration: float, ramp: float) -> None:
        baseline = self.target_rps
        steps = max(1, int(ramp / TICK))
        for s in range(1, steps + 1):
            self.target_rps = baseline + (peak - baseline) * (s / steps)
            await asyncio.sleep(TICK)
        self.target_rps = peak
        await asyncio.sleep(duration)
        for s in range(1, steps + 1):
            self.target_rps = peak + (baseline - peak) * (s / steps)
            await asyncio.sleep(TICK)
        self.target_rps = baseline

    async def _fire_one(self) -> None:
        if self._client is None:
            return
        self.in_flight += 1
        t0 = time.perf_counter()
        ok = False
        try:
            r = await self._client.get(
                f"{self.ingress_url}/read",
                params={"mode": self.mode, "limit": self.limit},
            )
            ok = r.status_code < 500
        except Exception:
            ok = False
        finally:
            self.in_flight -= 1
            dt = (time.perf_counter() - t0) * 1000.0
            self.completed_total += 1
            self._lat_sum += dt
            self._lat_count += 1
            self._bucket += 1
            if not ok:
                self.errors_total += 1

    async def _loop_main(self) -> None:
        carry = 0.0
        while self._running:
            count, carry = requests_this_tick(self.target_rps, TICK, carry)
            for _ in range(count):
                if self.in_flight >= MAX_IN_FLIGHT:
                    # back-pressure: system can't keep up, stop offering more
                    break
                self.sent_total += 1
                asyncio.create_task(self._fire_one())
            now = time.time()
            elapsed = now - self._bucket_start
            if elapsed >= 1.0:
                self.actual_rps = round(self._bucket / elapsed, 1)
                self._bucket = 0
                self._bucket_start = now
            await asyncio.sleep(TICK)

    # --- status (called from the control-plane thread) ---
    def status(self) -> dict:
        avg_lat = round(self._lat_sum / self._lat_count, 2) if self._lat_count else 0.0
        return {
            "running": self._running,
            "target_rps": round(self.target_rps, 1),
            "actual_rps": self.actual_rps,
            "mode": self.mode,
            "in_flight": self.in_flight,
            "sent_total": self.sent_total,
            "completed_total": self.completed_total,
            "errors_total": self.errors_total,
            "avg_latency_ms": avg_lat,
        }
