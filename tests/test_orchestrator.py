"""Unit tests for the control-plane Orchestrator using a mocked Docker client."""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "control-plane"))

from app.orchestrator import (  # noqa: E402
    MANAGED_LABEL,
    ROLE_LABEL,
    WORKER_ID_LABEL,
    Orchestrator,
    OrchestratorError,
)


def _fake_container(worker_id="w-abc12345", status="running", name="readissue-worker-abc12345"):
    c = MagicMock()
    c.id = "deadbeefcafe0000"
    c.name = name
    c.status = status
    c.labels = {MANAGED_LABEL: "true", ROLE_LABEL: "worker", WORKER_ID_LABEL: worker_id}
    c.attrs = {
        "NetworkSettings": {"Networks": {"readissue_net": {"IPAddress": "172.20.0.5"}}},
        "State": {"Health": {"Status": "healthy"}},
    }
    c.reload = MagicMock()
    return c


def test_list_instances_maps_fields():
    client = MagicMock()
    client.containers.list.return_value = [_fake_container()]
    orch = Orchestrator(client=client)
    out = orch.list_instances()
    assert len(out) == 1
    info = out[0]
    assert info["worker_id"] == "w-abc12345"
    assert info["ip"] == "172.20.0.5"
    assert info["health"] == "healthy"
    assert info["status"] == "running"


def test_create_instance_runs_with_labels_and_env():
    client = MagicMock()
    client.containers.list.return_value = []  # count_instances -> 0
    created = _fake_container()
    client.containers.run.return_value = created
    orch = Orchestrator(client=client)

    info = orch.create_instance()

    assert client.containers.run.called
    kwargs = client.containers.run.call_args.kwargs
    assert kwargs["network"] == "readissue_net"
    assert kwargs["labels"]["traefik.enable"] == "true"
    assert kwargs["labels"][MANAGED_LABEL] == "true"
    assert "WORKER_ID" in kwargs["environment"]
    assert kwargs["environment"]["POSTGRES_HOST"] == "postgres"
    assert info["worker_id"] == "w-abc12345"


def test_create_instance_respects_max_workers(monkeypatch):
    from app.config import settings as cfg
    monkeypatch.setattr(cfg, "max_workers", 2)
    client = MagicMock()
    # 2 running already -> at cap
    client.containers.list.return_value = [_fake_container(), _fake_container()]
    orch = Orchestrator(client=client)
    with pytest.raises(OrchestratorError):
        orch.create_instance()


def test_destroy_instance_removes_matching():
    client = MagicMock()
    c = _fake_container()
    client.containers.list.return_value = [c]
    orch = Orchestrator(client=client)
    assert orch.destroy_instance("w-abc12345") is True
    c.remove.assert_called_once_with(force=True)


def test_destroy_instance_not_found():
    client = MagicMock()
    client.containers.list.return_value = []
    orch = Orchestrator(client=client)
    assert orch.destroy_instance("nope") is False


def test_kill_random_kills_and_removes():
    client = MagicMock()
    c = _fake_container()
    client.containers.list.return_value = [c]
    orch = Orchestrator(client=client)
    wid = orch.kill_random()
    assert wid == "w-abc12345"
    c.kill.assert_called_once()
    c.remove.assert_called_once_with(force=True)


def test_kill_random_none_when_empty():
    client = MagicMock()
    client.containers.list.return_value = []
    orch = Orchestrator(client=client)
    assert orch.kill_random() is None
