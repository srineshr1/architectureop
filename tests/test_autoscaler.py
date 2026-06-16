"""Unit tests for the autoscaler decision logic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "control-plane"))

from app.autoscaler import DEFAULT_CONFIG, decide_scale  # noqa: E402


def cfg(**over):
    c = dict(DEFAULT_CONFIG)
    c["enabled"] = True
    c.update(over)
    return c


# signature: decide_scale(avg_cpu, max_p95, count, cfg, in_cooldown)

def test_disabled_never_scales():
    c = cfg(enabled=False)
    assert decide_scale(99, 999, 1, c, False) == "none"


def test_scale_up_below_min_even_in_cooldown():
    c = cfg(min=2, max=6)
    assert decide_scale(0, 0, 1, c, True) == "up"


def test_scale_down_above_max_even_in_cooldown():
    c = cfg(min=1, max=3)
    assert decide_scale(0, 0, 4, c, True) == "down"


def test_cooldown_blocks_threshold_scaling():
    c = cfg(cpu_high=65, p95_high=120, min=1, max=6)
    assert decide_scale(90, 500, 2, c, True) == "none"


def test_scale_up_on_high_cpu():
    c = cfg(cpu_high=65, max=6)
    assert decide_scale(80, 5, 2, c, False) == "up"


def test_scale_up_on_high_latency_even_if_cpu_low():
    # I/O-bound: CPU stays low but p95 spikes -> must still scale up.
    c = cfg(cpu_high=65, p95_high=120, max=6)
    assert decide_scale(30, 200, 1, c, False) == "up"


def test_no_scale_up_at_max():
    c = cfg(cpu_high=65, p95_high=120, max=3)
    assert decide_scale(95, 500, 3, c, False) == "none"


def test_scale_down_requires_low_cpu_and_low_latency():
    c = cfg(cpu_low=15, p95_low=25, min=1)
    assert decide_scale(5, 10, 3, c, False) == "down"


def test_no_scale_down_when_latency_still_high():
    # CPU low but latency elevated -> hold, don't scale down.
    c = cfg(cpu_low=15, p95_low=25, min=1)
    assert decide_scale(5, 90, 3, c, False) == "none"


def test_no_scale_down_at_min():
    c = cfg(cpu_low=15, p95_low=25, min=2)
    assert decide_scale(1, 1, 2, c, False) == "none"


def test_steady_zone_no_action():
    c = cfg(cpu_high=65, cpu_low=15, p95_high=120, p95_low=25, min=1, max=6)
    assert decide_scale(40, 60, 3, c, False) == "none"
