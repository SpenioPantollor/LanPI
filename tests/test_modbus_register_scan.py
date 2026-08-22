"""Tests for backend/tools/modbus_register_scan.py -- the bisection
probing algorithm (against a fake server with a known readable/
unreadable address pattern) and segment merging."""
import socket
import struct
import threading
import time

import pytest

from backend.tools import modbus, modbus_register_scan


@pytest.fixture(autouse=True)
def _bind_from_localhost_and_reset(monkeypatch):
    monkeypatch.setattr(modbus, "_eth0_source_ip", lambda: "127.0.0.1")
    monkeypatch.setattr(modbus, "_bind_to_eth0_device", lambda sock: None)
    with modbus_register_scan._lock:
        modbus_register_scan._state.update(
            {"running": False, "segments": [], "progress": 0, "total": 0}
        )
    yield
    modbus_register_scan.stop()


def _wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_merge_segments_combines_adjacent_matching_outcomes():
    segments = [
        {"start": 0, "end": 4, "readable": True, "message": None},
        {"start": 5, "end": 9, "readable": True, "message": None},
        {"start": 10, "end": 14, "readable": False, "message": "Modbus exception: Illegal Data Address"},
    ]
    merged = modbus_register_scan._merge_segments(segments)
    assert merged == [
        {"start": 0, "end": 9, "readable": True, "message": None},
        {"start": 10, "end": 14, "readable": False, "message": "Modbus exception: Illegal Data Address"},
    ]


def test_merge_segments_keeps_different_messages_separate():
    segments = [
        {"start": 0, "end": 4, "readable": False, "message": "Modbus exception: Illegal Data Address"},
        {"start": 5, "end": 9, "readable": False, "message": "Modbus exception: Illegal Function"},
    ]
    merged = modbus_register_scan._merge_segments(segments)
    assert len(merged) == 2


def test_start_rejects_bad_register_type():
    result = modbus_register_scan.start("1.2.3.4", "bogus_type", 1, 0, 10)
    assert result["ok"] is False
    assert "register type" in result["message"]


def test_start_rejects_start_after_end():
    result = modbus_register_scan.start("1.2.3.4", "holding_registers", 1, 100, 10)
    assert result["ok"] is False


def test_start_rejects_oversized_range():
    result = modbus_register_scan.start("1.2.3.4", "holding_registers", 1, 0, 50000)
    assert result["ok"] is False
    assert "too large" in result["message"]


def _run_fake_holding_register_server(readable_ranges: list[tuple]) -> int:
    """Serves FC3 reads: a request is only answered successfully if
    its entire [address, address+quantity) span falls inside one of
    `readable_ranges` -- any overlap with an unreadable address fails
    the whole request with Illegal Data Address, matching how a real
    device would reject a block read that touches unmapped registers."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(5)

    def is_fully_readable(address, quantity):
        return any(lo <= address and address + quantity - 1 <= hi for lo, hi in readable_ranges)

    def handle_one(conn):
        with conn:
            header = conn.recv(7)
            if len(header) < 7:
                return
            transaction_id, _protocol, length, unit_id = struct.unpack("!HHHB", header)
            pdu = conn.recv(length - 1)
            function_code, address, quantity = struct.unpack("!BHH", pdu)
            if is_fully_readable(address, quantity):
                data = b"".join(struct.pack("!H", 1) for _ in range(quantity))
                pdu_out = struct.pack("!BB", function_code, len(data)) + data
            else:
                pdu_out = struct.pack("!BB", function_code | 0x80, 2)  # Illegal Data Address
            conn.sendall(struct.pack("!HHHB", transaction_id, 0, len(pdu_out) + 1, unit_id) + pdu_out)

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


def test_scan_finds_readable_and_unreadable_boundaries(monkeypatch):
    monkeypatch.setattr(modbus_register_scan, "_PROBE_DELAY_SECONDS", 0.005)
    # Registers 0-9 readable, 10-19 unreadable, 20-29 readable.
    port = _run_fake_holding_register_server([(0, 9), (20, 29)])

    result = modbus_register_scan.start(
        "127.0.0.1", "holding_registers", unit_id=1, start_address=0, end_address=29, port=port, timeout=1.0,
    )
    assert result["ok"] is True
    assert _wait_until(lambda: modbus_register_scan.status()["running"] is False, timeout=10.0)

    segments = modbus_register_scan.status()["segments"]
    readable_ranges = [(s["start"], s["end"]) for s in segments if s["readable"]]
    unreadable_ranges = [(s["start"], s["end"]) for s in segments if s["readable"] is False]

    # Every readable segment found must itself be a subset of an
    # actually-readable range (no false positives), and the boundary
    # (register 10) must show up as unreadable, not readable.
    assert any(lo <= 0 and 9 <= hi for lo, hi in readable_ranges)
    assert any(lo <= 20 and 29 <= hi for lo, hi in readable_ranges)
    assert any(lo <= 10 <= hi for lo, hi in unreadable_ranges)

    # Every address in [0,29] is accounted for exactly once.
    total_covered = sum(s["end"] - s["start"] + 1 for s in segments)
    assert total_covered == 30


def test_stop_ends_scan_early(monkeypatch):
    monkeypatch.setattr(modbus_register_scan, "_PROBE_DELAY_SECONDS", 0.05)
    unused = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    unused.bind(("127.0.0.1", 0))
    port = unused.getsockname()[1]
    unused.close()

    modbus_register_scan.start(
        "127.0.0.1", "holding_registers", unit_id=1, start_address=0, end_address=1000, port=port, timeout=0.2,
    )
    assert _wait_until(lambda: modbus_register_scan.status()["progress"] > 0)

    modbus_register_scan.stop()
    time.sleep(0.5)
    assert modbus_register_scan.status()["running"] is False
