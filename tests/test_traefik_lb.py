"""Integration test: Traefik load-balances across worker instances.

Skips automatically unless the live lab is reachable (Traefik ingress on
localhost and at least 2 workers discovered). Run after:
    docker compose up -d traefik
    + at least two worker containers on the lab network.
"""
import collections
import os

import httpx
import pytest

INGRESS = os.environ.get("INGRESS_URL", "http://localhost:8080")
DASHBOARD = os.environ.get("TRAEFIK_API", "http://localhost:8081")


def _ingress_ready() -> bool:
    try:
        r = httpx.get(f"{DASHBOARD}/api/http/services", timeout=2.0)
        data = r.json()
        for s in data:
            if "workers" in s.get("name", ""):
                servers = s.get("loadBalancer", {}).get("servers", [])
                return len(servers) >= 2
    except Exception:
        return False
    return False


pytestmark = pytest.mark.skipif(
    not _ingress_ready(),
    reason="live lab not running with >=2 discovered workers",
)


def test_round_robin_hits_multiple_workers():
    seen = collections.Counter()
    with httpx.Client(timeout=5.0) as client:
        for _ in range(20):
            r = client.get(f"{INGRESS}/read", params={"limit": 1})
            assert r.status_code == 200
            seen[r.json()["worker_id"]] += 1
    # Traffic must have been distributed across at least 2 distinct workers.
    assert len(seen) >= 2, f"expected >=2 workers, saw {dict(seen)}"
