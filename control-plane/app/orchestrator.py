"""Docker-SDK orchestration of worker instances.

Spawns/destroys worker containers on the shared lab network. Every managed
container carries identifying labels (for discovery + cleanup) and Traefik
labels (so the load balancer auto-routes to it -- Task 4).
"""
from __future__ import annotations

import uuid
from typing import Dict, List, Optional

import docker
from docker.errors import NotFound

from .config import settings

MANAGED_LABEL = "readissue.managed"
ROLE_LABEL = "readissue.role"
WORKER_ID_LABEL = "readissue.worker_id"

# Traefik routing labels shared by all workers: they form one load-balanced
# service ("workers") behind a single catch-all router.
TRAEFIK_LABELS = {
    "traefik.enable": "true",
    "traefik.http.routers.workers.rule": "PathPrefix(`/`)",
    "traefik.http.routers.workers.entrypoints": "web",
    "traefik.http.services.workers.loadbalancer.server.port": "8000",
}


class OrchestratorError(RuntimeError):
    pass


class Orchestrator:
    def __init__(self, client: Optional["docker.DockerClient"] = None) -> None:
        self._client = client or docker.from_env()

    # --- helpers ---
    def _container_to_info(self, c) -> Dict:
        c.reload()
        net = c.attrs.get("NetworkSettings", {}).get("Networks", {})
        ip = None
        if settings.lab_network in net:
            ip = net[settings.lab_network].get("IPAddress")
        elif net:
            ip = next(iter(net.values())).get("IPAddress")
        health = None
        state = c.attrs.get("State", {})
        if isinstance(state, dict) and "Health" in state:
            health = state["Health"].get("Status")
        return {
            "worker_id": c.labels.get(WORKER_ID_LABEL, c.name),
            "container_id": c.id[:12],
            "name": c.name,
            "status": c.status,
            "health": health,
            "ip": ip,
        }

    # --- queries ---
    def list_instances(self) -> List[Dict]:
        containers = self._client.containers.list(
            all=True,
            filters={"label": f"{MANAGED_LABEL}=true"},
        )
        return [self._container_to_info(c) for c in containers]

    def count_instances(self) -> int:
        return len(
            self._client.containers.list(
                filters={"label": f"{MANAGED_LABEL}=true", "status": "running"}
            )
        )

    # --- mutations ---
    def create_instance(self, extra_env: Optional[dict] = None) -> Dict:
        running = self.count_instances()
        if running >= settings.max_workers:
            raise OrchestratorError(
                f"max workers reached ({running}/{settings.max_workers})"
            )
        worker_id = f"w-{uuid.uuid4().hex[:8]}"
        name = f"readissue-worker-{worker_id[2:]}"
        labels = {
            MANAGED_LABEL: "true",
            ROLE_LABEL: "worker",
            WORKER_ID_LABEL: worker_id,
            **TRAEFIK_LABELS,
        }
        try:
            container = self._client.containers.run(
                settings.worker_image,
                name=name,
                detach=True,
                network=settings.lab_network,
                labels=labels,
                environment=settings.worker_env(worker_id, extra_env),
                restart_policy={"Name": "no"},
            )
        except Exception as exc:  # noqa: BLE001
            raise OrchestratorError(f"failed to create worker: {exc}") from exc
        return self._container_to_info(container)

    def destroy_instance(self, worker_id: str) -> bool:
        containers = self._client.containers.list(
            all=True,
            filters={"label": f"{WORKER_ID_LABEL}={worker_id}"},
        )
        if not containers:
            return False
        for c in containers:
            try:
                c.remove(force=True)
            except NotFound:
                pass
        return True

    def destroy_one(self) -> Optional[str]:
        """Remove a single running worker (used by scale-down)."""
        running = self._client.containers.list(
            filters={"label": f"{MANAGED_LABEL}=true", "status": "running"}
        )
        if not running:
            return None
        target = running[-1]
        wid = target.labels.get(WORKER_ID_LABEL, target.name)
        target.remove(force=True)
        return wid

    def kill_random(self) -> Optional[str]:
        """Forcibly kill a random running worker (instance-crash scenario)."""
        import random

        running = self._client.containers.list(
            filters={"label": f"{MANAGED_LABEL}=true", "status": "running"}
        )
        if not running:
            return None
        target = random.choice(running)
        wid = target.labels.get(WORKER_ID_LABEL, target.name)
        target.kill()  # SIGKILL: simulates a hard crash, not a graceful stop
        target.remove(force=True)
        return wid

    def destroy_all(self) -> int:
        containers = self._client.containers.list(
            all=True,
            filters={"label": f"{MANAGED_LABEL}=true"},
        )
        n = 0
        for c in containers:
            try:
                c.remove(force=True)
                n += 1
            except NotFound:
                pass
        return n
