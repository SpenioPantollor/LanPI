"""Tests for backend/tools/modbus.py's hand-rolled Modbus TCP client.

Validation-only cases need no network. The protocol-level cases run a
tiny one-shot fake Modbus TCP server on localhost (same shape as the
fake server improvised earlier this session to verify float32 decoding
before real-hardware testing), so the request/response framing and
decoding are exercised end-to-end without needing real hardware.
"""
import socket
import struct
import threading

import pytest

from backend.tools import modbus


@pytest.fixture(autouse=True)
def _bind_from_localhost(monkeypatch):
    # read() sources its connection from eth0's address (see module
    # docstring) -- point that at localhost so tests don't need a real
    # eth0 interface/IP configured.
    monkeypatch.setattr(modbus, "_eth0_source_ip", lambda: "127.0.0.1")


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            return data
        data += chunk
    return data


def _run_fake_server(build_response) -> int:
    """One-shot server: accepts a single connection, decodes the MBAP+PDU
    request, hands it to build_response(transaction_id, unit_id,
    function_code, address, quantity) -> bytes, sends that back."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(1)

    def handle():
        server.settimeout(5)
        try:
            conn, _ = server.accept()
        except socket.timeout:
            server.close()
            return
        with conn:
            header = _recv_exact(conn, 7)
            transaction_id, _protocol, length, unit_id = struct.unpack("!HHHB", header)
            pdu = _recv_exact(conn, length - 1)
            function_code, address, quantity = struct.unpack("!BHH", pdu)
            conn.sendall(build_response(transaction_id, unit_id, function_code, address, quantity))
        server.close()

    threading.Thread(target=handle, daemon=True).start()
    return port


def _register_response(transaction_id, unit_id, function_code, values):
    data = b"".join(struct.pack("!H", v) for v in values)
    pdu = struct.pack("!BB", function_code, len(data)) + data
    return struct.pack("!HHHB", transaction_id, 0, len(pdu) + 1, unit_id) + pdu


def _run_fake_device_id_server(build_response) -> int:
    """Like _run_fake_server, but for FC43/MEI14 requests -- a different
    PDU shape from the read functions, and potentially more than one
    request/response round-trip on the same connection if the device
    signals "more follows". Hands each request to
    build_response(transaction_id, unit_id, read_device_id_code,
    object_id) -> bytes."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    server.listen(1)

    def handle():
        server.settimeout(5)
        try:
            conn, _ = server.accept()
        except socket.timeout:
            server.close()
            return
        with conn:
            while True:
                header = _recv_exact(conn, 7)
                if len(header) < 7:
                    break
                transaction_id, _protocol, length, unit_id = struct.unpack("!HHHB", header)
                pdu = _recv_exact(conn, length - 1)
                _fc, _mei, read_device_id_code, object_id = struct.unpack("!BBBB", pdu)
                conn.sendall(build_response(transaction_id, unit_id, read_device_id_code, object_id))
        server.close()

    threading.Thread(target=handle, daemon=True).start()
    return port


def _device_id_response(transaction_id, unit_id, objects, more_follows=0x00, next_object_id=0x00):
    body = struct.pack("!BBBBBB", 0x2B, 0x0E, 0x02, 0x02, more_follows, next_object_id) + bytes([len(objects)])
    for obj_id, value in objects.items():
        body += bytes([obj_id, len(value)]) + value
    return struct.pack("!HHHB", transaction_id, 0, len(body) + 1, unit_id) + body


def _exception_response(transaction_id, unit_id, function_code, exception_code):
    pdu = struct.pack("!BB", function_code | 0x80, exception_code)
    return struct.pack("!HHHB", transaction_id, 0, len(pdu) + 1, unit_id) + pdu


def test_reads_holding_registers():
    port = _run_fake_server(
        lambda tid, uid, fc, addr, qty: _register_response(tid, uid, fc, [1234, 5678])
    )
    result = modbus.read("127.0.0.1", unit_id=1, function_code=3, address=0, quantity=2, port=port)
    assert result["ok"] is True
    assert result["function"] == "read_holding_registers"
    assert result["address"] == 0
    assert result["quantity"] == 2
    assert result["values"] == [1234, 5678]
    assert result["response_time_ms"] >= 0
    assert len(result["raw_request"]) == 24  # 7-byte MBAP + 5-byte read PDU, hex-encoded
    assert len(result["raw_response"]) > 0


def test_reads_coils_as_bits():
    port = _run_fake_server(
        lambda tid, uid, fc, addr, qty: struct.pack("!HHHB", tid, 0, 4, uid)
        + struct.pack("!BB", fc, 1)
        + bytes([0b00000101])  # coil0=on, coil1=off, coil2=on
    )
    result = modbus.read("127.0.0.1", unit_id=1, function_code=1, address=0, quantity=3, port=port)
    assert result["ok"] is True
    assert result["values"] == [True, False, True]


def test_modbus_exception_response_is_decoded():
    port = _run_fake_server(
        lambda tid, uid, fc, addr, qty: _exception_response(tid, uid, fc, 2)  # Illegal Data Address
    )
    result = modbus.read("127.0.0.1", unit_id=1, function_code=4, address=0, quantity=1, port=port)
    assert result["ok"] is False
    assert result["message"] == "Modbus exception: Illegal Data Address"
    assert result["response_time_ms"] >= 0
    assert len(result["raw_response"]) > 0


def test_unknown_exception_code_falls_back_to_generic_message():
    port = _run_fake_server(
        lambda tid, uid, fc, addr, qty: _exception_response(tid, uid, fc, 99)
    )
    result = modbus.read("127.0.0.1", unit_id=1, function_code=3, address=0, quantity=1, port=port)
    assert result["ok"] is False
    assert result["message"] == "Modbus exception: code 99"


def test_rejects_empty_host():
    assert modbus.read("", unit_id=1, function_code=3, address=0, quantity=1) == {
        "ok": False,
        "message": "host is required",
    }


def test_rejects_unknown_function_code():
    result = modbus.read("1.2.3.4", unit_id=1, function_code=9, address=0, quantity=1)
    assert result["ok"] is False
    assert "function code" in result["message"]


def test_rejects_out_of_range_unit_id():
    result = modbus.read("1.2.3.4", unit_id=256, function_code=3, address=0, quantity=1)
    assert result == {"ok": False, "message": "unit ID must be 0-255"}


def test_rejects_quantity_over_register_limit():
    result = modbus.read("1.2.3.4", unit_id=1, function_code=3, address=0, quantity=126)
    assert result["ok"] is False
    assert "1-125" in result["message"]


def test_rejects_quantity_over_coil_limit():
    result = modbus.read("1.2.3.4", unit_id=1, function_code=1, address=0, quantity=2001)
    assert result["ok"] is False
    assert "1-2000" in result["message"]


def test_no_eth0_address_returns_clear_message(monkeypatch):
    monkeypatch.setattr(modbus, "_eth0_source_ip", lambda: None)
    result = modbus.read("1.2.3.4", unit_id=1, function_code=3, address=0, quantity=1)
    assert result["ok"] is False
    assert "eth0 has no IP address" in result["message"]


def test_device_identification_success():
    objects = {0x00: b"Acme Corp", 0x01: b"AC-100", 0x05: b"Model X"}
    port = _run_fake_device_id_server(lambda tid, uid, code, obj_id: _device_id_response(tid, uid, objects))

    result = modbus.read_device_identification("127.0.0.1", unit_id=1, port=port)

    assert result["ok"] is True
    assert result["supported"] is True
    assert result["objects"]["vendor_name"] == "Acme Corp"
    assert result["objects"]["product_code"] == "AC-100"
    assert result["objects"]["model_name"] == "Model X"
    assert result["response_time_ms"] >= 0


def test_device_identification_follows_more_follows_across_requests():
    first = {0x00: b"Acme", 0x01: b"AC-1", 0x02: b"1.0"}
    second = {0x03: b"http://acme.example", 0x04: b"Widget", 0x05: b"WX1", 0x06: b"App"}

    def responder(tid, uid, code, obj_id):
        if obj_id == 0x00:
            return _device_id_response(tid, uid, first, more_follows=0xFF, next_object_id=0x03)
        return _device_id_response(tid, uid, second)

    port = _run_fake_device_id_server(responder)
    result = modbus.read_device_identification("127.0.0.1", unit_id=1, port=port)

    assert result["ok"] is True
    assert result["objects"]["vendor_name"] == "Acme"
    assert result["objects"]["vendor_url"] == "http://acme.example"
    assert result["objects"]["user_application_name"] == "App"


def test_device_identification_not_supported_is_distinguished_from_a_real_error():
    def responder(tid, uid, code, obj_id):
        pdu = struct.pack("!BB", 0x2B | 0x80, 1)  # Illegal Function
        return struct.pack("!HHHB", tid, 0, len(pdu) + 1, uid) + pdu

    port = _run_fake_device_id_server(responder)
    result = modbus.read_device_identification("127.0.0.1", unit_id=1, port=port)

    assert result["ok"] is False
    assert result["supported"] is False
    assert "not supported" in result["message"]


def test_device_identification_rejects_empty_host():
    assert modbus.read_device_identification("", unit_id=1) == {"ok": False, "message": "host is required"}
