"""Passive MNDP (MikroTik Neighbor Discovery Protocol) discovery.

Packets arrive via backend/capture/dispatcher.py's single shared
tcpdump capture (v0.2.3 Foundation #3) rather than this module running
its own dedicated tcpdump process -- see that module's docstring for
why. _handle_packet() does the IPv4/UDP-port-5678 filter that used to
be a BPF filter at the tcpdump level, then hands off to the same
_parse_mndp_payload() TLV parser as before.

MNDP is a UDP broadcast (port 5678), not an L2 EtherType/LLC frame like
LLDP/CDP, so the framing includes a full IPv4 + UDP header before the
MNDP payload:

  [Ethernet(14)] [IPv4, length = (byte[14] & 0x0F) * 4] [UDP(8)] [MNDP payload]

MNDP payload: 2-byte header + 2-byte sequence number, then TLVs
(type(2) length(2) value(length)), all big-endian except uptime, which
MikroTik encodes little-endian.

TLV type values and the little-endian uptime quirk are per publicly
documented MNDP reimplementations (Wireshark's mndp dissector, various
open-source MNDP clients) -- not verified against RouterOS source, so
treat as best-effort pending live confirmation against a real router.

Caches every distinct neighbor seen, keyed by the Ethernet frame's
source MAC (not the payload's own optional MAC-address TLV, which
could in principle be missing) -- same fix and reasoning as
backend/discovery/lldp.py (2026-08-25): a single-neighbor cache would
flicker/overwrite itself the moment more than one MNDP-speaking
device was reachable through the test switch. Same shape as
traffic_stats.py's _talkers dict, same bounding (_MAX_NEIGHBORS,
oldest-by-last-seen evicted over the cap).
"""

from __future__ import annotations

import struct
import threading
import time

from backend.capture import dispatcher

_MNDP_PORT = 5678
_MAX_NEIGHBORS = 500
_DEFAULT_STALE_AFTER = 60.0  # user-set: purge a neighbor entirely after 60s of silence

_lock = threading.Lock()
_neighbors: dict[str, dict] = {}  # mac -> {..fields.., mac, last_seen}
_started_interfaces: set[str] = set()


def _parse_mndp_payload(payload: bytes) -> dict:
    neighbor = {
        "mac_address": None,
        "identity": None,
        "platform": None,
        "version": None,
        "board": None,
        "uptime_seconds": None,
        "software_id": None,
        "interface_name": None,
        "ipv4_address": None,
    }
    if len(payload) < 4:
        return neighbor

    i = 4  # skip 2-byte header + 2-byte sequence number
    n = len(payload)
    while i + 4 <= n:
        tlv_type, tlv_len = struct.unpack("!HH", payload[i:i + 4])
        i += 4
        value = payload[i:i + tlv_len]
        if len(value) < tlv_len:
            break
        i += tlv_len

        if tlv_type == 0x0001 and len(value) == 6:
            neighbor["mac_address"] = ":".join(f"{b:02x}" for b in value)
        elif tlv_type == 0x0005:
            neighbor["identity"] = value.decode("utf-8", errors="replace")
        elif tlv_type == 0x0007:
            neighbor["version"] = value.decode("utf-8", errors="replace")
        elif tlv_type == 0x0008:
            neighbor["platform"] = value.decode("utf-8", errors="replace")
        elif tlv_type == 0x000A and len(value) == 4:
            neighbor["uptime_seconds"] = struct.unpack("<I", value)[0]
        elif tlv_type == 0x000B:
            neighbor["software_id"] = value.decode("utf-8", errors="replace")
        elif tlv_type == 0x000C:
            neighbor["board"] = value.decode("utf-8", errors="replace")
        elif tlv_type == 0x0010:
            neighbor["interface_name"] = value.decode("utf-8", errors="replace")
        elif tlv_type == 0x0011 and len(value) == 4:
            neighbor["ipv4_address"] = ".".join(str(b) for b in value)

    return neighbor


def _handle_packet(interface: str, packet: bytes) -> None:
    if len(packet) < 14 + 20 + 8:
        return
    ethertype = struct.unpack("!H", packet[12:14])[0]
    if ethertype != 0x0800:  # IPv4
        return
    ip_header_len = (packet[14] & 0x0F) * 4
    ip_proto = packet[14 + 9]
    if ip_proto != 17:  # UDP
        return
    udp_offset = 14 + ip_header_len
    mndp_offset = udp_offset + 8
    if len(packet) < udp_offset + 4:
        return
    sport, dport = struct.unpack("!HH", packet[udp_offset:udp_offset + 4])
    if _MNDP_PORT not in (sport, dport):
        return
    if len(packet) < mndp_offset:
        return

    mac = ":".join(f"{b:02x}" for b in packet[6:12])
    neighbor = _parse_mndp_payload(packet[mndp_offset:])
    neighbor["mac"] = mac
    neighbor["last_seen"] = time.time()
    with _lock:
        _neighbors[mac] = neighbor
        if len(_neighbors) > _MAX_NEIGHBORS:
            oldest_mac = min(_neighbors, key=lambda m: _neighbors[m]["last_seen"])
            if oldest_mac != mac:
                del _neighbors[oldest_mac]


def start_listener(interface: str = "eth0") -> None:
    with _lock:
        if interface in _started_interfaces:
            return
        _started_interfaces.add(interface)
    dispatcher.start_listener(interface)
    dispatcher.register_handler(lambda packet: _handle_packet(interface, packet))


def get_neighbors(interface: str = "eth0", stale_after: float = _DEFAULT_STALE_AFTER) -> dict:
    start_listener(interface)

    now = time.time()
    with _lock:
        stale_macs = [mac for mac, n in _neighbors.items() if now - n["last_seen"] > stale_after]
        for mac in stale_macs:
            del _neighbors[mac]
        neighbors = list(_neighbors.values())

    fresh = []
    for neighbor in neighbors:
        entry = dict(neighbor)
        entry["age_seconds"] = int(now - neighbor["last_seen"])
        fresh.append(entry)
    fresh.sort(key=lambda n: n["age_seconds"])

    return {"interface": interface, "present": len(fresh) > 0, "neighbors": fresh}
