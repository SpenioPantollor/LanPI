"""Tests for backend/tools/modbus_poll.py's background polling loop
and statistics tracking."""
import socket
import struct
import threading
import time

import pytest

from backend.tools import modbus, modbus_poll


@pytest.fixture(autouse=True)
def _bind_from_localhost_and_reset(monkeypatch):
    monkeypatch.setattr(modbus, "_eth0_source_ip", lambda: "127.0.0.1")
    with modbus_poll._lock:
        modbus_poll._state.update(
            {
                "running": False, "requests": 0, "successful": 0, "timeouts": 0, "exceptions": 0,
                "min_ms": None, "avg_ms": None, "max_ms": None, "_sum_ms": 0.0, "_ms_count": 0,
            }
        )
    yield
    modbus_poll.stop()


def _wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_start_rejects_empty_host():
    result = modbus_poll.start("", port=502, unit_id=1, function_code=3, address=0, quantity=1)
    assert result == {"ok": False, "message": "host is required"}


def test_start_rejects_interval_below_floor():
    result = modbus_poll.start("1.2.3.4", 502, 1, 3, 0, 1, interval_ms=10)
    assert result["ok"] is False
    assert "interval" in result["message"]


def _run_fake_polling_server(value: int) -> int:
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
            data = struct.pack("!H", value)
            pdu = struct.pack("!BB", 3, len(data)) + data
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


def test_poll_accumulates_stats_and_last_value(monkeypatch):
    port = _run_fake_polling_server(value=77)

    result = modbus_poll.start(
        "127.0.0.1", port, unit_id=1, function_code=3, address=0, quantity=1, interval_ms=200,
    )
    assert result["ok"] is True

    assert _wait_until(lambda: modbus_poll.status()["requests"] >= 3, timeout=5.0)
    status = modbus_poll.status()

    assert status["value"] == 77
    assert status["successful"] >= 3
    assert status["timeouts"] == 0
    assert status["exceptions"] == 0
    assert status["failure_percent"] == 0.0
    assert status["min_ms"] is not None
    assert status["avg_ms"] is not None
    assert status["max_ms"] is not None
    assert status["min_ms"] <= status["avg_ms"] <= status["max_ms"]


def test_stop_ends_polling(monkeypatch):
    port = _run_fake_polling_server(value=1)
    modbus_poll.start("127.0.0.1", port, unit_id=1, function_code=3, address=0, quantity=1, interval_ms=200)
    assert _wait_until(lambda: modbus_poll.status()["requests"] >= 1)

    modbus_poll.stop()
    count_at_stop = modbus_poll.status()["requests"]
    time.sleep(0.5)
    assert modbus_poll.status()["requests"] == count_at_stop
    assert modbus_poll.status()["running"] is False
