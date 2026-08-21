"""API-layer validation tests via FastAPI's TestClient.

Only exercises request validation (pydantic + each tool's own business
validation), not real network/subprocess behavior -- those are covered
by the per-module tests. Uses TestClient(app) without the `with`
context manager so the startup event (which starts the background
tcpdump listener threads) never fires; this suite doesn't need them
and they'd just be background noise pointed at a nonexistent eth0 on
whatever machine runs pytest.

Requires fastapi+httpx (see requirements-dev.txt) -- skipped entirely
if unavailable rather than failing the whole test run.
"""
import pytest

pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402

client = TestClient(app)


def test_health():
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_status_reports_version_from_version_file():
    response = client.get("/api/status")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"hostname", "platform", "lanpi_version", "backend_uptime_seconds"}
    assert body["lanpi_version"] != "unknown"


def test_modbus_read_rejects_bad_function_code_with_200_not_500():
    # function_code has no pydantic-level range constraint -- the check
    # happens inside modbus.read() itself, so this should come back as
    # a normal 200 with ok:False, not a validation error or a crash.
    response = client.post(
        "/api/tools/modbus/read",
        json={"host": "1.2.3.4", "unit_id": 1, "function_code": 9, "address": 0, "quantity": 1},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "function code" in body["message"]


def test_modbus_read_requires_host_field():
    response = client.post(
        "/api/tools/modbus/read",
        json={"unit_id": 1, "function_code": 3, "address": 0, "quantity": 1},
    )
    assert response.status_code == 422  # pydantic: host is a required field


def test_port_scan_start_rejects_bad_range():
    response = client.post(
        "/api/tools/port-scan/start",
        json={"host": "192.168.1.1", "port_range": "not-a-range"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "1-65535" in body["message"]


def test_eth0_mode_rejects_unknown_mode():
    response = client.post("/api/network/eth0/mode", json={"mode": "bogus"})
    assert response.status_code == 200
    assert response.json() == {"ok": False, "message": "mode must be passive, dhcp, or static"}


def test_modbus_templates_list_returns_a_list_of_dicts():
    response = client.get("/api/tools/modbus/templates")
    assert response.status_code == 200
    templates = response.json()
    assert isinstance(templates, list)
    assert len(templates) > 0
    assert all("id" in t for t in templates)


def test_tcp_test_requires_host_and_port():
    response = client.post("/api/tools/tcp-test", json={})
    assert response.status_code == 422
