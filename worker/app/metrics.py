"""In-memory request metrics for a worker instance.

Pure/standalone (no FastAPI or DB imports) so it can be unit-tested in
isolation. Tracks counters plus a rolling window of recent latencies for
percentile estimation.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict


class Metrics:
    def __init__(self, worker_id: str, window: int = 512):
        self.worker_id = worker_id
        self._lock = threading.Lock()
        self._latencies_ms: Deque[float] = deque(maxlen=window)
        self.requests_total = 0
        self.errors_total = 0
        self.in_flight = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self._start = time.time()

    # --- request lifecycle ---
    def begin(self) -> None:
        with self._lock:
            self.in_flight += 1

    def end(self, latency_ms: float, error: bool = False) -> None:
        with self._lock:
            self.in_flight = max(0, self.in_flight - 1)
            self.requests_total += 1
            if error:
                self.errors_total += 1
            self._latencies_ms.append(latency_ms)

    # --- cache accounting ---
    def record_cache(self, hit: bool) -> None:
        with self._lock:
            if hit:
                self.cache_hits += 1
            else:
                self.cache_misses += 1

    # --- derived ---
    @staticmethod
    def _percentile(sorted_vals, pct: float) -> float:
        if not sorted_vals:
            return 0.0
        if len(sorted_vals) == 1:
            return round(sorted_vals[0], 3)
        # nearest-rank style index
        k = (len(sorted_vals) - 1) * pct
        f = int(k)
        c = min(f + 1, len(sorted_vals) - 1)
        if f == c:
            return round(sorted_vals[f], 3)
        d = k - f
        return round(sorted_vals[f] * (1 - d) + sorted_vals[c] * d, 3)

    def snapshot(self) -> Dict:
        with self._lock:
            vals = sorted(self._latencies_ms)
            total_cache = self.cache_hits + self.cache_misses
            hit_ratio = (self.cache_hits / total_cache) if total_cache else 0.0
            return {
                "worker_id": self.worker_id,
                "requests_total": self.requests_total,
                "errors_total": self.errors_total,
                "in_flight": self.in_flight,
                "latency_p50_ms": self._percentile(vals, 0.50),
                "latency_p95_ms": self._percentile(vals, 0.95),
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "cache_hit_ratio": round(hit_ratio, 4),
                "uptime_s": round(time.time() - self._start, 1),
            }
