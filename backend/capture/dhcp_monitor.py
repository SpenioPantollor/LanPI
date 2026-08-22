"""Passive rogue/unexpected DHCP server detection on the TEST PORT
(V0.3 backlog item).

Watches the shared packet-capture dispatcher (backend/capture/
dispatcher.py) for DHCP server replies (OFFER/ACK, UDP port 67) rather
than running a separate tcpdump listener -- same one-capture-many-
consumers shape as traffic_stats.py/modbus_traffic.py/ip_conflict.py.

There's no "known good server" list to compare against -- LanPi has no
device registry yet (see README's Roadmap) and no configuration step
where an operator would register one. Instead this just tracks every
distinct DHCP server actually seen answering on the segment, by its
DHCP "server identifier" (option 54, falling back to the IP header's
source address if a packet omits it): IP, MAC, offer/ack counts, and a
small sample of IPs it has handed out. `multiple_servers_detected` is
true the moment a second distinct server shows up -- more than one
DHCP server answering on the same segment is the actual rogue-DHCP
symptom (clients randomly getting leases/gateways from whichever
server answers first), regardless of which one is "correct". The
operator reads the list and judges which server (if any) doesn't
belong; LanPi doesn't guess.
"""

from __future__ import annotations

import struct
import threading
import time

from backend.capture import dispatcher

_DHCP_SERVER_PORT = 67
_DHCP_MAGIC_COOKIE = bytes([99, 130, 83, 99])
_OFFER = 2
_ACK = 5
_MAX_SERVERS = 50
_MAX_OFFERED_IPS_SAMPLE = 5

_lock = threading.Lock()
_servers: dict[str, dict] = {}  # server_ip -> {mac, offers, acks, first_seen, last_seen, offered_ips}
_started = False


def _parse_ipv4_udp(packet: bytes):
    """Returns (src_mac, src_ip, dst_ip, src_port, dst_port, udp_payload),
    or None if this isn't a parseable Ethernet+IPv4+UDP packet."""
    if len(packet) < 14 + 20 + 8:
        return None
    ethertype = struct.unpack("!H", packet[12:14])[0]
    if ethertype != 0x0800:
        return None
    ihl = (packet[14] & 0x0F) * 4
    proto = packet[23]
    if proto != 17:  # UDP
        return None
    if len(packet) < 14 + ihl + 8:
        return None
    src_mac = ":".join(f"{b:02x}" for b in packet[6:12])
    src_ip = ".".join(str(b) for b in packet[26:30])
    dst_ip = ".".join(str(b) for b in packet[30:34])
    udp_offset = 14 + ihl
    src_port, dst_port = struct.unpack("!HH", packet[udp_offset:udp_offset + 4])
    payload = packet[udp_offset + 8:]
    return src_mac, src_ip, dst_ip, src_port, dst_port, payload


def _parse_dhcp_options(options: bytes) -> dict[int, bytes]:
    parsed = {}
    i = 0
    while i < len(options):
        code = options[i]
        if code == 0xFF:  # End
            break
        if code == 0x00:  # Pad
            i += 1
            continue
        if i + 1 >= len(options):
            break
        length = options[i + 1]
        value = options[i + 2:i + 2 + length]
        if len(value) < length:
            break
        parsed[code] = value
        i += 2 + length
    return parsed


def _parse_dhcp(payload: bytes):
    """Returns (message_type, offered_ip, server_identifier_ip), or
    None if `payload` isn't a parseable BOOTP/DHCP message."""
    if len(payload) < 240 or payload[236:240] != _DHCP_MAGIC_COOKIE:
        return None
    yiaddr = ".".join(str(b) for b in payload[16:20])
    options = _parse_dhcp_options(payload[240:])
    message_type_raw = options.get(53)
    if not message_type_raw:
        return None
    message_type = message_type_raw[0]
    server_identifier = None
    identifier_raw = options.get(54)
    if identifier_raw and len(identifier_raw) == 4:
        server_identifier = ".".join(str(b) for b in identifier_raw)
    return message_type, yiaddr, server_identifier


def handle_packet(packet: bytes) -> None:
    parsed = _parse_ipv4_udp(packet)
    if parsed is None:
        return
    src_mac, src_ip, _dst_ip, src_port, _dst_port, payload = parsed
    if src_port != _DHCP_SERVER_PORT:
        return

    parsed_dhcp = _parse_dhcp(payload)
    if parsed_dhcp is None:
        return
    message_type, offered_ip, server_identifier = parsed_dhcp
    if message_type not in (_OFFER, _ACK):
        return

    server_ip = server_identifier or src_ip
    now = time.time()

    with _lock:
        entry = _servers.get(server_ip)
        if entry is None:
            if len(_servers) >= _MAX_SERVERS:
                return
            entry = {
                "mac": src_mac, "offers": 0, "acks": 0,
                "first_seen": now, "last_seen": now, "offered_ips": set(),
            }
            _servers[server_ip] = entry
        entry["mac"] = src_mac
        entry["last_seen"] = now
        if message_type == _OFFER:
            entry["offers"] += 1
        else:
            entry["acks"] += 1
        if offered_ip and offered_ip != "0.0.0.0" and len(entry["offered_ips"]) < _MAX_OFFERED_IPS_SAMPLE:
            entry["offered_ips"].add(offered_ip)


def start_listener(interface: str = "eth0") -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    dispatcher.start_listener(interface)
    dispatcher.register_handler(handle_packet)


def get_servers() -> dict:
    with _lock:
        servers = [
            {
                "server_ip": ip,
                "mac": entry["mac"],
                "offers": entry["offers"],
                "acks": entry["acks"],
                "first_seen": entry["first_seen"],
                "last_seen": entry["last_seen"],
                "offered_ip_sample": sorted(entry["offered_ips"]),
            }
            for ip, entry in _servers.items()
        ]
        servers.sort(key=lambda s: s["first_seen"])
        return {"servers": servers, "multiple_servers_detected": len(servers) > 1}


def reset() -> dict:
    with _lock:
        _servers.clear()
    return {"ok": True, "message": "DHCP server tracking reset"}
