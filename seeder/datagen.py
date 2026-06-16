"""Dummy data generation for the ReadIssue products table.

Kept free of any DB/IO so it can be unit-tested in isolation.
"""
from __future__ import annotations

import random
from typing import Iterator

CATEGORIES = [
    "electronics", "books", "home", "garden", "toys",
    "sports", "grocery", "fashion", "automotive", "beauty",
]

_ADJECTIVES = ["Compact", "Deluxe", "Eco", "Ultra", "Smart", "Classic", "Pro", "Mini"]
_NOUNS = ["Widget", "Gadget", "Gizmo", "Device", "Tool", "Kit", "Module", "Unit"]


def make_row(i: int, rng: random.Random) -> tuple:
    """Return a single product row tuple for index ``i``.

    Deterministic given the same rng seed so tests are reproducible.
    """
    category = CATEGORIES[i % len(CATEGORIES)]
    name = f"{rng.choice(_ADJECTIVES)} {rng.choice(_NOUNS)} {i}"
    sku = f"SKU-{i:08d}"
    price = round(rng.uniform(1.0, 999.0), 2)
    stock = rng.randint(0, 1000)
    description = (
        f"{name} in the {category} category. "
        f"High quality item number {i}."
    )
    return (sku, name, category, price, stock, description)


def generate_rows(count: int, seed: int = 42) -> Iterator[tuple]:
    """Yield ``count`` product row tuples."""
    if count < 0:
        raise ValueError("count must be >= 0")
    rng = random.Random(seed)
    for i in range(count):
        yield make_row(i, rng)
