"""Unit tests for the metrics collector's pure helpers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "control-plane"))

from app.collector import (  # noqa: E402
    compute_cpu_percent,
    compute_mem_mb,
    derive_rps,
)


def _stats(total, pre_total, system, pre_system, online=4, mem=200 * 1024 * 1024):
    return {
        "cpu_stats": {
            "cpu_usage": {"total_usage": total},
            "system_cpu_usage": system,
            "online_cpus": online,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": pre_total},
            "system_cpu_usage": pre_system,
        },
        "memory_stats": {"usage": mem, "stats": {"inactive_file": 0}},
    }


def test_cpu_percent_basic():
    # cpu_delta=10, system_delta=100, online=4 -> 10/100*4*100 = 40%
    s = _stats(total=110, pre_total=100, system=1100, pre_system=1000, online=4)
    assert compute_cpu_percent(s) == 40.0


def test_cpu_percent_full_load_single_cpu():
    # cpu_delta == system_delta, 1 cpu -> 100%
    s = _stats(total=200, pre_total=100, system=1100, pre_system=1000, online=1)
    assert compute_cpu_percent(s) == 100.0


def test_cpu_percent_zero_system_delta():
    s = _stats(total=110, pre_total=100, system=1000, pre_system=1000)
    assert compute_cpu_percent(s) == 0.0


def test_cpu_percent_malformed():
    assert compute_cpu_percent({}) == 0.0


def test_mem_mb_subtracts_cache():
    s = {"memory_stats": {"usage": 300 * 1024 * 1024,
                          "stats": {"inactive_file": 100 * 1024 * 1024}}}
    assert compute_mem_mb(s) == 200.0


def test_derive_rps():
    assert derive_rps(100, 50, 1.0) == 50.0
    assert derive_rps(100, 50, 2.0) == 25.0


def test_derive_rps_no_prev():
    assert derive_rps(100, None, 1.0) == 0.0


def test_derive_rps_counter_reset():
    # worker restarted -> current < prev -> 0
    assert derive_rps(5, 100, 1.0) == 0.0
