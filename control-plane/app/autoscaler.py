"""Threshold-based auto-scaling control loop.

Watches the latest metrics snapshot and scales the worker pool up/down based
on average CPU, within [min, max] bounds and subject to a cooldown that
prevents flapping. The decision logic is a pure function so it can be
unit-tested without Docker or timers.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional


DEFAULT_CONFIG = {
    "enabled": False,
    "min": 1,
    "max": 6,
    "cpu_high": 50.0,    # scale up when avg CPU% at/above this
    "cpu_low": 12.0,     # scale down when avg CPU% at/below this
    "p95_high": 120.0,   # OR scale up when worker p95 latency (ms) at/above this
    "p95_low": 25.0,     # require p95 below this (with cpu_low) to scale down
    "cooldown_s": 12.0,  # min seconds between scaling actions
}


def decide_scale(avg_cpu: float, max_p95: float, count: int, cfg: dict,
                 in_cooldown: bool) -> str:
    """Return 'up', 'down', or 'none'.

    Scales on CPU *or* latency: read tiers are usually I/O-bound, so they
    saturate on p95 latency long before CPU pegs. Bounds (min/max) are enforced
    even during cooldown; threshold-driven scaling is suppressed during cooldown
    to avoid flapping.
    """
    if not cfg.get("enabled"):
        return "none"
    if count < cfg["min"]:
        return "up"
    if count > cfg["max"]:
        return "down"
    if in_cooldown:
        return "none"
    cpu_hot = avg_cpu >= cfg["cpu_high"]
    lat_hot = max_p95 >= cfg["p95_high"]
    if (cpu_hot or lat_hot) and count < cfg["max"]:
        return "up"
    # Only scale down when BOTH cpu and latency are comfortably low.
    if avg_cpu <= cfg["cpu_low"] and max_p95 <= cfg["p95_low"] and count > cfg["min"]:
        return "down"
    return "none"


class AutoScaler:
    def __init__(
        self,
        get_metrics: Callable[[], dict],
        scale_up: Callable[[], object],
        scale_down: Callable[[], object],
        interval: float = 3.0,
        max_workers: int = 6,
    ):
        self.get_metrics = get_metrics
        self.scale_up = scale_up
        self.scale_down = scale_down
        self.interval = interval
        self.config = dict(DEFAULT_CONFIG)
        self.config["max"] = max_workers
        self._last_action_ts = 0.0
        self.last_reason = "idle"
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def update_config(self, patch: dict) -> None:
        for k in ("enabled", "min", "max", "cpu_high", "cpu_low",
                  "p95_high", "p95_low", "cooldown_s"):
            if k in patch and patch[k] is not None:
                self.config[k] = type(DEFAULT_CONFIG[k])(patch[k])

    def status(self) -> dict:
        return {
            **self.config,
            "last_action_ts": round(self._last_action_ts, 1),
            "last_reason": self.last_reason,
        }

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
                await self._tick()
            except Exception as exc:  # keep loop alive
                self.last_reason = f"error: {exc}"
            await asyncio.sleep(self.interval)

    async def _tick(self) -> None:
        snap = self.get_metrics() or {}
        sys = snap.get("system", {})
        avg_cpu = float(sys.get("avg_cpu_pct", 0) or 0)
        max_p95 = float(sys.get("max_latency_p95_ms", 0) or 0)
        count = int(sys.get("instance_count", 0) or 0)
        now = time.time()
        in_cooldown = (now - self._last_action_ts) < self.config["cooldown_s"]

        action = decide_scale(avg_cpu, max_p95, count, self.config, in_cooldown)
        if action == "up":
            await asyncio.to_thread(self.scale_up)
            self._last_action_ts = now
            self.last_reason = f"scaled up (cpu={avg_cpu:.0f}%, p95={max_p95:.0f}ms, n={count})"
        elif action == "down":
            await asyncio.to_thread(self.scale_down)
            self._last_action_ts = now
            self.last_reason = f"scaled down (cpu={avg_cpu:.0f}%, p95={max_p95:.0f}ms, n={count})"
