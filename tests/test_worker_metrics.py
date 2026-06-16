"""Unit tests for the worker Metrics class (no framework/DB)."""
import importlib.util
from pathlib import Path

# Load worker/app/metrics.py directly by path. Both the worker and the control
# plane define a top-level package named ``app``; importing via the package
# name collides when the whole suite is collected together. metrics.py is
# standalone (no relative imports), so a direct file load is clean and isolated.
_METRICS_PATH = Path(__file__).resolve().parents[1] / "worker" / "app" / "metrics.py"
_spec = importlib.util.spec_from_file_location("worker_metrics_mod", _METRICS_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
Metrics = _mod.Metrics


def test_initial_snapshot():
    m = Metrics("w1")
    snap = m.snapshot()
    assert snap["worker_id"] == "w1"
    assert snap["requests_total"] == 0
    assert snap["in_flight"] == 0
    assert snap["latency_p50_ms"] == 0.0


def test_in_flight_tracking():
    m = Metrics("w1")
    m.begin()
    m.begin()
    assert m.snapshot()["in_flight"] == 2
    m.end(10.0)
    assert m.snapshot()["in_flight"] == 1


def test_request_and_error_counts():
    m = Metrics("w1")
    m.begin(); m.end(5.0)
    m.begin(); m.end(7.0, error=True)
    snap = m.snapshot()
    assert snap["requests_total"] == 2
    assert snap["errors_total"] == 1


def test_percentiles_monotonic():
    m = Metrics("w1")
    for v in range(1, 101):  # 1..100 ms
        m.begin(); m.end(float(v))
    snap = m.snapshot()
    assert snap["latency_p50_ms"] <= snap["latency_p95_ms"]
    # p95 of 1..100 should be near 95
    assert 90.0 <= snap["latency_p95_ms"] <= 100.0


def test_cache_hit_ratio():
    m = Metrics("w1")
    m.record_cache(hit=True)
    m.record_cache(hit=True)
    m.record_cache(hit=False)
    snap = m.snapshot()
    assert snap["cache_hits"] == 2
    assert snap["cache_misses"] == 1
    assert abs(snap["cache_hit_ratio"] - (2 / 3)) < 1e-3


def test_in_flight_never_negative():
    m = Metrics("w1")
    m.end(1.0)  # end without begin
    assert m.snapshot()["in_flight"] == 0
