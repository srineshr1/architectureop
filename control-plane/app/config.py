"""Control plane configuration (env-driven)."""
from __future__ import annotations

import os


class Settings:
    def __init__(self) -> None:
        self.worker_image = os.environ.get("WORKER_IMAGE", "readissue-worker:latest")
        self.lab_network = os.environ.get("LAB_NETWORK", "readissue_net")
        self.max_workers = int(os.environ.get("MAX_WORKERS", "8"))

        # Passed through to every worker container. Workers always run inside
        # the lab network, so this must be the service name ("postgres").
        self.postgres_host = os.environ.get("POSTGRES_HOST", "postgres")
        self.postgres_port = os.environ.get("POSTGRES_PORT", "5432")
        self.postgres_user = os.environ.get("POSTGRES_USER", "readissue")
        self.postgres_password = os.environ.get("POSTGRES_PASSWORD", "readissue")
        self.postgres_db = os.environ.get("POSTGRES_DB", "readissue")
        # The collector's OWN Postgres connection. Same as workers when the
        # control plane runs in-network; override to "localhost" for host dev
        # (the published 5432 port) WITHOUT affecting what workers receive.
        self.collector_pg_host = os.environ.get("COLLECTOR_PG_HOST", self.postgres_host)
        self.redis_host = os.environ.get("REDIS_HOST", "redis")
        self.redis_port = os.environ.get("REDIS_PORT", "6379")
        self.db_pool_size = os.environ.get("DB_POOL_SIZE", "10")

        # Where the load generator sends traffic (the Traefik ingress).
        # In-container default targets the traefik service; on the host use
        # http://localhost:8080.
        self.ingress_url = os.environ.get("INGRESS_URL", "http://traefik:80")

    def worker_env(self, worker_id: str, extra: dict | None = None) -> dict:
        env = {
            "WORKER_ID": worker_id,
            "POSTGRES_HOST": self.postgres_host,
            "POSTGRES_PORT": self.postgres_port,
            "POSTGRES_USER": self.postgres_user,
            "POSTGRES_PASSWORD": self.postgres_password,
            "POSTGRES_DB": self.postgres_db,
            "REDIS_HOST": self.redis_host,
            "REDIS_PORT": self.redis_port,
            "DB_POOL_SIZE": self.db_pool_size,
        }
        if extra:
            env.update(extra)
        return env


settings = Settings()
