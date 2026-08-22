"""Tests for backend/tools/modbus_unit_scan.py -- classification logic
(directly) and the background start/status/stop lifecycle against a
fake multi-unit Modbus server."""
import socket
import struct
import threading
import time

import pytest

from backend.tools import modbus, modbus_unit_scan


@pytest.fixture(autouse=True)
def _bind_from_localhost_and_reset(monkeypatch):
    monkeypatch.setattr(modbus, "_eth0_source_ip", lambda: "127.0.0.1")
    monkeypatch.setattr(modbus, "_bind_to_eth0_device", lambda sock: None)
    with modbus_unit_scan._lock:
        modbus_unit_scan._state.update(
            {"running": False, "results": [], "progress": 0, "total": 0}
        )
    yield
    modbus_unit_scan.stop()


def _wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_classify_success_is_responding():
    status, detail = modbus_unit_scan._classify({"ok": True})
    assert status == "responding"
    assert "FC3" in detail


def test_classify_modbus_exception_is_still_responding():
    status, detail = modbus_unit_scan._classify({"ok": False, "message": "Modbus exception: Illegal Data Address"})
    assert status == "responding"
    assert "Illegal Data Address" in detail


def test_classify_timeout_is_no_response():
    status, detail = modbus_unit_scan._classify({"ok": False, "message": "timeout -- no response from device"})
    assert status == "no_response"


def test_start_rejects_empty_host():
    result = modbus_unit_scan.start("", 1, 10)
    assert result == {"ok": False, "message": "host is required"}


def test_start_rejects_start_after_end():
    result = modbus_unit_scan.start("1.2.3.4", 10, 5)
    assert result["ok"] is False
    assert "start unit ID" in result["message"]


def test_start_rejects_out_of_range_unit_id():
    result = modbus_unit_scan.start("1.2.3.4", 0, 300)
    assert result["ok"] is False
    assert "0-255" in result["message"]


def _run_fake_multi_unit_server(responding_units: set) -> int:
    """A server that only responds (with a valid FC3 reply) to unit
    IDs in `responding_units`; other unit IDs get an Illegal Data
    Address exception, distinguishing "responding with an exception"
    from a real non-response in the scan results."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(5)

    def handle_one(conn):
        with conn:
            header = conn.recv(7)
            if len(header) < 7:
                return
            transaction_id, _protocol, length, unit_id = struct.unpack("!HHHB", header)
            conn.recv(length - 1)
            if unit_id in responding_units:
                data = struct.pack("!H", 42)
                pdu = struct.pack("!BB", 3, len(data)) + data
            else:
                pdu = struct.pack("!BB", 3 | 0x80, 2)  # Illegal Data Address
            conn.sendall(struct.pack("!HHHB", transaction_id, 0, len(pdu) + 1, unit_id) + pdu)

    def accept_loop():
        server.settimeout(10)
        try:
            while True:
                conn, _ = server.accept()
                handle_one(conn)
        except socket.timeout:
            pass
        finally:
            server.close()

    threading.Thread(target=accept_loop, daemon=True).start()
    return port


def test_scan_classifies_responding_and_exception_units(monkeypatch):
    monkeypatch.setattr(modbus_unit_scan, "_PROBE_DELAY_SECONDS", 0.01)
    port = _run_fake_multi_unit_server({2, 4})

    result = modbus_unit_scan.start("127.0.0.1", 1, 4, port=port, timeout=1.0)
    assert result["ok"] is True

    assert _wait_until(lambda: modbus_unit_scan.status()["running"] is False)

    results = {r["unit_id"]: r["status"] for r in modbus_unit_scan.status()["results"]}
    assert results[1] == "responding"  # exception response still counts as responding
    assert results[2] == "responding"
    assert results[3] == "responding"
    assert results[4] == "responding"


def test_stop_ends_scan_early(monkeypatch):
    monkeypatch.setattr(modbus_unit_scan, "_PROBE_DELAY_SECONDS", 0.05)
    # Nothing listens on this port -- every probe will time out, so the
    # scan stays running long enough to be stopped mid-way.
    unused = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    unused.bind(("127.0.0.1", 0))
    port = unused.getsockname()[1]
    unused.close()

    modbus_unit_scan.start("127.0.0.1", 1, 255, port=port, timeout=0.2)
    assert _wait_until(lambda: len(modbus_unit_scan.status()["results"]) >= 1)

    modbus_unit_scan.stop()
    count_at_stop = len(modbus_unit_scan.status()["results"])
    time.sleep(0.5)
    assert len(modbus_unit_scan.status()["results"]) == count_at_stop
    assert modbus_unit_scan.status()["running"] is False
