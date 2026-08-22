"""Tests for backend/capture/dhcp_monitor.py's passive DHCP-server
detection.

Feeds hand-built raw Ethernet+IPv4+UDP+BOOTP frames straight to
handle_packet(), the same shape the dispatcher hands every listener
after stripping pcap record framing -- no real capture involved.
"""
from __future__ import annotations

import struct

import pytest

from backend.capture import dhcp_monitor

_MAGIC_COOKIE = bytes([99, 130, 83, 99])


def _mac_bytes(s: str) -> bytes:
    return bytes(int(b, 16) for b in s.split(":"))


def _ip_bytes(ip: str) -> bytes:
    return bytes(int(o) for o in ip.split("."))


def _udp_packet(src_ip: str, dst_ip: str, src_port: int, dst_port: int, src_mac: str, payload: bytes) -> bytes:
    eth = _mac_bytes("ff:ff:ff:ff:ff:ff") + _mac_bytes(src_mac) + struct.pack("!H", 0x0800)
    ip_header = bytearray(20)
    ip_header[0] = 0x45
    ip_header[9] = 17  # UDP
    ip_header[12:16] = _ip_bytes(src_ip)
    ip_header[16:20] = _ip_bytes(dst_ip)
    udp_header = struct.pack("!HHHH", src_port, dst_port, 8 + len(payload), 0)
    return eth + bytes(ip_header) + udp_header + payload


def _dhcp_payload(message_type: int, yiaddr: str = "0.0.0.0", server_identifier: str | None = None) -> bytes:
    fixed = bytearray(236)
    fixed[16:20] = _ip_bytes(yiaddr)
    options = bytes([53, 1, message_type])
    if server_identifier:
        options += bytes([54, 4]) + _ip_bytes(server_identifier)
    options += bytes([0xFF])
    return bytes(fixed) + _MAGIC_COOKIE + options


def _server_packet(server_ip: str, message_type: int, mac: str = "cc:cc:cc:cc:cc:cc",
                    yiaddr: str = "10.0.0.100", server_identifier: str | None = None) -> bytes:
    payload = _dhcp_payload(message_type, yiaddr, server_identifier or server_ip)
    return _udp_packet(server_ip, "255.255.255.255", 67, 68, mac, payload)


def _client_packet(message_type: int) -> bytes:
    payload = _dhcp_payload(message_type)
    return _udp_packet("0.0.0.0", "255.255.255.255", 68, 67, "dd:dd:dd:dd:dd:dd", payload)


@pytest.fixture(autouse=True)
def _reset_state():
    dhcp_monitor.reset()
    yield
    dhcp_monitor.reset()


def test_ignores_non_dhcp_udp_traffic():
    packet = _udp_packet("10.0.0.1", "10.0.0.2", 67, 68, "cc:cc:cc:cc:cc:cc", b"not dhcp")
    dhcp_monitor.handle_packet(packet)
    assert dhcp_monitor.get_servers()["servers"] == []


def test_client_messages_are_ignored():
    dhcp_monitor.handle_packet(_client_packet(message_type=1))  # DISCOVER
    dhcp_monitor.handle_packet(_client_packet(message_type=3))  # REQUEST
    assert dhcp_monitor.get_servers()["servers"] == []


def test_offer_registers_a_server():
    dhcp_monitor.handle_packet(_server_packet("10.0.0.1", message_type=2, yiaddr="10.0.0.50"))

    servers = dhcp_monitor.get_servers()["servers"]
    assert len(servers) == 1
    assert servers[0]["server_ip"] == "10.0.0.1"
    assert servers[0]["mac"] == "cc:cc:cc:cc:cc:cc"
    assert servers[0]["offers"] == 1
    assert servers[0]["acks"] == 0
    assert servers[0]["offered_ip_sample"] == ["10.0.0.50"]
    assert dhcp_monitor.get_servers()["multiple_servers_detected"] is False


def test_ack_counted_separately_from_offer():
    dhcp_monitor.handle_packet(_server_packet("10.0.0.1", message_type=2))
    dhcp_monitor.handle_packet(_server_packet("10.0.0.1", message_type=5))

    server = dhcp_monitor.get_servers()["servers"][0]
    assert server["offers"] == 1
    assert server["acks"] == 1


def test_server_identifier_option_used_as_the_key():
    # A relay/NAT scenario where the IP header's source differs from
    # the DHCP server identifier the server itself claims.
    dhcp_monitor.handle_packet(_server_packet("10.0.0.254", message_type=2, server_identifier="10.0.0.1"))

    servers = dhcp_monitor.get_servers()["servers"]
    assert len(servers) == 1
    assert servers[0]["server_ip"] == "10.0.0.1"


def test_two_distinct_servers_flags_multiple_servers_detected():
    dhcp_monitor.handle_packet(_server_packet("10.0.0.1", message_type=2))
    dhcp_monitor.handle_packet(_server_packet("10.0.0.99", message_type=2, mac="ee:ee:ee:ee:ee:ee"))

    result = dhcp_monitor.get_servers()
    assert len(result["servers"]) == 2
    assert result["multiple_servers_detected"] is True


def test_reset_clears_tracked_servers():
    dhcp_monitor.handle_packet(_server_packet("10.0.0.1", message_type=2))
    dhcp_monitor.reset()
    assert dhcp_monitor.get_servers() == {"servers": [], "multiple_servers_detected": False}
