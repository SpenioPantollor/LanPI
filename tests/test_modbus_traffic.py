"""Tests for backend/capture/modbus_traffic.py's passive Modbus TCP
parser and request/response correlation.

Feeds hand-built raw Ethernet+IPv4+TCP frames straight to
handle_packet(), the same shape the dispatcher hands every listener
after stripping pcap record framing -- no real capture involved.
"""
import struct

import pytest

from backend.capture import modbus_traffic

_MODBUS_PORT = 502


def _mac(s: str) -> bytes:
    return bytes(int(b, 16) for b in s.split(":"))


def _ip_bytes(ip: str) -> bytes:
    return bytes(int(o) for o in ip.split("."))


def _tcp_packet(src_ip: str, dst_ip: str, src_port: int, dst_port: int, payload: bytes) -> bytes:
    eth = _mac("11:11:11:11:11:11") + _mac("22:22:22:22:22:22") + struct.pack("!H", 0x0800)
    ip_header = bytearray(20)
    ip_header[0] = 0x45
    ip_header[9] = 6  # TCP
    ip_header[12:16] = _ip_bytes(src_ip)
    ip_header[16:20] = _ip_bytes(dst_ip)
    tcp_header = bytearray(20)
    struct.pack_into("!HH", tcp_header, 0, src_port, dst_port)
    tcp_header[12] = 5 << 4  # data offset: 5 words (20 bytes), no options
    return eth + bytes(ip_header) + bytes(tcp_header) + payload


def _mbap(transaction_id: int, unit_id: int, pdu: bytes) -> bytes:
    return struct.pack("!HHHB", transaction_id, 0, len(pdu) + 1, unit_id) + pdu


def _request(transaction_id: int, unit_id: int, function_code: int, address: int = 0, quantity: int = 1) -> bytes:
    return _mbap(transaction_id, unit_id, struct.pack("!BHH", function_code, address, quantity))


def _response(transaction_id: int, unit_id: int, function_code: int, values: list) -> bytes:
    data = b"".join(struct.pack("!H", v) for v in values)
    return _mbap(transaction_id, unit_id, struct.pack("!BB", function_code, len(data)) + data)


def _exception(transaction_id: int, unit_id: int, function_code: int, exception_code: int) -> bytes:
    return _mbap(transaction_id, unit_id, struct.pack("!BB", function_code | 0x80, exception_code))


def _request_packet(client_ip, server_ip, transaction_id, unit_id, function_code, client_port=50000):
    return _tcp_packet(client_ip, server_ip, client_port, _MODBUS_PORT, _request(transaction_id, unit_id, function_code))


def _response_packet(client_ip, server_ip, transaction_id, unit_id, function_code, values, client_port=50000):
    return _tcp_packet(server_ip, client_ip, _MODBUS_PORT, client_port, _response(transaction_id, unit_id, function_code, values))


def _exception_packet(client_ip, server_ip, transaction_id, unit_id, function_code, exception_code, client_port=50000):
    return _tcp_packet(server_ip, client_ip, _MODBUS_PORT, client_port, _exception(transaction_id, unit_id, function_code, exception_code))


@pytest.fixture(autouse=True)
def _reset_state():
    modbus_traffic.reset()
    yield
    modbus_traffic.reset()


def test_ignores_non_modbus_traffic():
    packet = _tcp_packet("10.0.0.1", "10.0.0.2", 12345, 80, b"GET / HTTP/1.1\r\n\r\n")
    modbus_traffic.handle_packet(packet)
    assert modbus_traffic.get_stats()["relationships"] == []


def test_tracks_request_then_response():
    modbus_traffic.handle_packet(_request_packet("10.0.0.10", "10.0.0.20", 1, unit_id=1, function_code=3))
    modbus_traffic.handle_packet(_response_packet("10.0.0.10", "10.0.0.20", 1, unit_id=1, function_code=3, values=[42]))

    relationships = modbus_traffic.get_stats()["relationships"]
    assert len(relationships) == 1
    rel = relationships[0]
    assert rel["client_ip"] == "10.0.0.10"
    assert rel["server_ip"] == "10.0.0.20"
    assert rel["unit_id"] == 1
    assert rel["function_code"] == 3
    assert rel["requests"] == 1
    assert rel["responses"] == 1
    assert rel["exceptions"] == 0
    assert rel["missing"] == 0
    assert rel["avg_ms"] is not None
    assert rel["min_ms"] == rel["max_ms"] == rel["avg_ms"]


def test_counts_exception_response():
    modbus_traffic.handle_packet(_request_packet("10.0.0.10", "10.0.0.20", 5, unit_id=1, function_code=3))
    modbus_traffic.handle_packet(_exception_packet("10.0.0.10", "10.0.0.20", 5, unit_id=1, function_code=3, exception_code=2))

    rel = modbus_traffic.get_stats()["relationships"][0]
    assert rel["responses"] == 1
    assert rel["exceptions"] == 1


def test_multiple_clients_against_same_server_are_separate_rows():
    modbus_traffic.handle_packet(_request_packet("10.0.0.10", "10.0.0.20", 1, unit_id=1, function_code=3))
    modbus_traffic.handle_packet(_request_packet("10.0.0.11", "10.0.0.20", 1, unit_id=1, function_code=3))

    relationships = modbus_traffic.get_stats()["relationships"]
    client_ips = {r["client_ip"] for r in relationships}
    assert client_ips == {"10.0.0.10", "10.0.0.11"}
    assert all(r["server_ip"] == "10.0.0.20" for r in relationships)


def test_different_unit_ids_on_same_connection_are_separate_rows():
    modbus_traffic.handle_packet(_request_packet("10.0.0.10", "10.0.0.20", 1, unit_id=1, function_code=3))
    modbus_traffic.handle_packet(_request_packet("10.0.0.10", "10.0.0.20", 2, unit_id=2, function_code=3))

    relationships = modbus_traffic.get_stats()["relationships"]
    assert {r["unit_id"] for r in relationships} == {1, 2}


def test_response_with_no_matching_request_is_not_attributed():
    modbus_traffic.handle_packet(_response_packet("10.0.0.10", "10.0.0.20", 99, unit_id=1, function_code=3, values=[1]))
    assert modbus_traffic.get_stats()["relationships"] == []


def test_stale_pending_request_is_counted_missing(monkeypatch):
    monkeypatch.setattr(modbus_traffic, "_PENDING_TIMEOUT_SECONDS", 0.01)
    modbus_traffic.handle_packet(_request_packet("10.0.0.10", "10.0.0.20", 1, unit_id=1, function_code=3))

    import time
    time.sleep(0.05)

    # Any packet handled after the timeout triggers the prune pass;
    # get_stats() also prunes on its own before returning.
    rel = modbus_traffic.get_stats()["relationships"][0]
    assert rel["missing"] == 1


def test_reset_clears_relationships_and_pending():
    modbus_traffic.handle_packet(_request_packet("10.0.0.10", "10.0.0.20", 1, unit_id=1, function_code=3))
    modbus_traffic.reset()
    assert modbus_traffic.get_stats()["relationships"] == []
