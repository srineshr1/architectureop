"""Unit tests for the seeder data generator (no DB required)."""
import sys
from pathlib import Path

import pytest

# Make the seeder package importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "seeder"))

from datagen import CATEGORIES, generate_rows, make_row  # noqa: E402


def test_generate_rows_count():
    rows = list(generate_rows(100))
    assert len(rows) == 100


def test_generate_rows_zero():
    assert list(generate_rows(0)) == []


def test_generate_rows_negative_raises():
    with pytest.raises(ValueError):
        list(generate_rows(-1))


def test_row_shape_and_types():
    rows = list(generate_rows(10))
    for row in rows:
        sku, name, category, price, stock, description = row
        assert sku.startswith("SKU-")
        assert isinstance(name, str) and name
        assert category in CATEGORIES
        assert 1.0 <= price <= 999.0
        assert 0 <= stock <= 1000
        assert isinstance(description, str) and description


def test_generation_is_deterministic():
    a = list(generate_rows(50))
    b = list(generate_rows(50))
    assert a == b


def test_categories_round_robin():
    rows = list(generate_rows(len(CATEGORIES) * 2))
    cats = [r[2] for r in rows]
    # first cycle equals the category list in order
    assert cats[: len(CATEGORIES)] == CATEGORIES


def test_sku_is_unique_and_indexed():
    rows = list(generate_rows(1000))
    skus = {r[0] for r in rows}
    assert len(skus) == 1000
