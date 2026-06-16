"""Unit tests for the load generator's pure pacing logic."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "control-plane"))

from app.loadgen import requests_this_tick  # noqa: E402


def test_zero_rps():
    assert requests_this_tick(0, 0.1, 0.0) == (0, 0.0)


def test_integer_rate():
    # 100 rps * 0.1s = 10 per tick
    count, carry = requests_this_tick(100, 0.1, 0.0)
    assert count == 10
    assert abs(carry) < 1e-9


def test_fractional_carry_accumulates():
    # 15 rps * 0.1 = 1.5 per tick -> 1 then 2 then 1 ... averages 1.5
    carry = 0.0
    counts = []
    for _ in range(10):
        c, carry = requests_this_tick(15, 0.1, carry)
        counts.append(c)
    # over 1 second (10 ticks) should sum to ~15
    assert sum(counts) == 15


def test_low_rate_eventually_fires():
    # 2 rps * 0.1 = 0.2 per tick -> fires on the 5th tick
    carry = 0.0
    fired_ticks = []
    for t in range(10):
        c, carry = requests_this_tick(2, 0.1, carry)
        if c:
            fired_ticks.append(t)
    # ~2 fires over 1 second
    assert len(fired_ticks) == 2


def test_high_rate():
    count, _ = requests_this_tick(1000, 0.1, 0.0)
    assert count == 100
