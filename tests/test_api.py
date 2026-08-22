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
    assert set(body) == {
        "hostname", "platform", "lanpi_version", "backend_uptime_seconds", "capture_dispatcher",
    }
    assert body["lanpi_version"] != "unknown"
    assert set(body["capture_dispatcher"]) == {
        "tcpdump_available", "capture_running", "last_packet_at", "seconds_since_last_packet",
    }


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


def test_modbus_decode_endpoint():
    response = client.post("/api/tools/modbus/decode", json={"values": [1234], "byte_order": "ABCD"})
    assert response.status_code == 200
    body = response.json()
    assert body["uint16"] == 1234
    assert "uint32" not in body


def test_modbus_device_id_requires_host():
    response = client.post("/api/tools/modbus/device-id", json={"unit_id": 1})
    assert response.status_code == 422


def test_modbus_unit_scan_start_rejects_bad_range():
    response = client.post(
        "/api/tools/modbus/unit-scan/start",
        json={"host": "1.2.3.4", "start_unit": 10, "end_unit": 5},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False


def test_modbus_unit_scan_status_and_stop_when_idle():
    assert client.get("/api/tools/modbus/unit-scan/status").status_code == 200
    stop_response = client.post("/api/tools/modbus/unit-scan/stop")
    assert stop_response.status_code == 200
    assert stop_response.json() == {"ok": True, "message": "no scan running"}


def test_modbus_register_scan_start_rejects_bad_register_type():
    response = client.post(
        "/api/tools/modbus/register-scan/start",
        json={
            "host": "1.2.3.4", "register_type": "bogus", "unit_id": 1,
            "start_address": 0, "end_address": 10,
        },
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_modbus_poll_start_rejects_bad_interval():
    response = client.post(
        "/api/tools/modbus/poll/start",
        json={
            "host": "1.2.3.4", "unit_id": 1, "function_code": 3, "address": 0, "quantity": 1,
            "interval_ms": 1,
        },
    )
    assert response.status_code == 200
    assert response.json()["ok"] is False


def test_modbus_traffic_get_and_reset():
    # Reset first -- module-level state, not test-order-independent
    # otherwise, since another test file might have populated it.
    assert client.post("/api/tools/modbus/traffic/reset").json()["ok"] is True

    response = client.get("/api/tools/modbus/traffic")
    assert response.status_code == 200
    assert response.json() == {"relationships": []}


def test_eth0_link_history_get_and_reset():
    # Reset first -- module-level state, not test-order-independent
    # otherwise, since test_link_history.py may have left events behind.
    assert client.post("/api/network/eth0/history/reset").json()["ok"] is True

    response = client.get("/api/network/eth0/history")
    assert response.status_code == 200
    assert response.json() == {"events": []}


def test_ip_conflicts_get_and_reset():
    assert client.post("/api/network/ip-conflicts/reset").json()["ok"] is True

    response = client.get("/api/network/ip-conflicts")
    assert response.status_code == 200
    assert response.json() == {"conflicts": [], "tracked_ips": 0}


def test_dhcp_servers_get_and_reset():
    assert client.post("/api/network/dhcp-servers/reset").json()["ok"] is True

    response = client.get("/api/network/dhcp-servers")
    assert response.status_code == 200
    assert response.json() == {"servers": [], "multiple_servers_detected": False}
