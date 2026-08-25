"""Passive LLDP neighbor discovery on a given interface.

Packets arrive via backend/capture/dispatcher.py's single shared
tcpdump capture (v0.2.3 Foundation #3) rather than this module running
its own dedicated tcpdump process -- see that module's docstring for
why. _handle_packet() does the LLDP EtherType filter that used to be a
BPF filter at the tcpdump level, then hands off to the same
_parse_lldpdu() TLV parser as before.

Caches every distinct neighbor seen, keyed by source MAC, not just the
single most-recently-seen one -- user-reported 2026-08-25: once more
than one LLDP-sending device was actually reachable through the test
switch (an engineering PC and a Siemens device both visible), the
Dashboard's LLDP card visibly flickered/jumped between them, each
overwriting the other's cached entry every ~30s. Same shape as
traffic_stats.py's _talkers dict (keyed by MAC, no interface
indirection -- dispatcher.py only ever captures on one interface at a
time anyway, see its own docstring), bounded the same way too
(_MAX_NEIGHBORS, oldest-by-last-seen evicted over the cap) so a
long-running session on a busy segment doesn't grow unbounded. API
handlers read the cache directly so requests never block waiting on
the network.
"""

from __future__ import annotations

import struct
import threading
import time

from backend.capture import dispatcher

_LLDP_ETHERTYPE = 0x88CC
_MAX_NEIGHBORS = 500
_DEFAULT_STALE_AFTER = 150.0

_lock = threading.Lock()
_neighbors: dict[str, dict] = {}  # mac -> {..fields.., mac, last_seen}
_started_interfaces: set[str] = set()


def _parse_chassis_id(subtype: int, value: bytes) -> str:
    if subtype == 4 and len(value) == 6:  # MAC address
        return ":".join(f"{b:02x}" for b in value)
    return value.decode("utf-8", errors="replace")


def _parse_port_id(subtype: int, value: bytes) -> str:
    if subtype == 3 and len(value) == 6:  # MAC address
        return ":".join(f"{b:02x}" for b in value)
    return value.decode("utf-8", errors="replace")


def _parse_management_address(value: bytes) -> str | None:
    if len(value) < 2:
        return None
    addr_len = value[0]
    addr_subtype = value[1]
    addr = value[2:1 + addr_len]
    if addr_subtype == 1 and len(addr) == 4:  # IPv4
        return ".".join(str(b) for b in addr)
    if addr_subtype == 2 and len(addr) == 16:  # IPv6
        return ":".join(addr[i:i + 2].hex() for i in range(0, 16, 2))
    return None


def _parse_lldpdu(payload: bytes) -> dict:
    neighbor = {
        "chassis_id": None,
        "port_id": None,
        "port_description": None,
        "system_name": None,
        "system_description": None,
        "management_ip": None,
        "vlan": None,
    }
    i = 0
    n = len(payload)
    while i + 2 <= n:
        header = struct.unpack("!H", payload[i:i + 2])[0]
        tlv_type = header >> 9
        tlv_len = header & 0x1FF
        i += 2
        value = payload[i:i + tlv_len]
        i += tlv_len
        if len(value) < tlv_len:
            break

        if tlv_type == 0:  # End of LLDPDU
            break
        if tlv_type == 1 and value:
            neighbor["chassis_id"] = _parse_chassis_id(value[0], value[1:])
        elif tlv_type == 2 and value:
            neighbor["port_id"] = _parse_port_id(value[0], value[1:])
        elif tlv_type == 4:
            neighbor["port_description"] = value.decode("utf-8", errors="replace")
        elif tlv_type == 5:
            neighbor["system_name"] = value.decode("utf-8", errors="replace")
        elif tlv_type == 6:
            neighbor["system_description"] = value.decode("utf-8", errors="replace").strip()
        elif tlv_type == 8:
            addr = _parse_management_address(value)
            if addr:
                neighbor["management_ip"] = addr
        elif tlv_type == 127 and len(value) >= 6:
            oui = value[0:3]
            org_subtype = value[3]
            if oui == b"\x00\x80\xc2" and org_subtype == 3:  # 802.1 Port VLAN ID
                neighbor["vlan"] = struct.unpack("!H", value[4:6])[0]

    return neighbor


def _handle_packet(interface: str, packet: bytes) -> None:
    if len(packet) < 14:
        return
    ethertype = struct.unpack("!H", packet[12:14])[0]
    if ethertype != _LLDP_ETHERTYPE:
        return

    mac = ":".join(f"{b:02x}" for b in packet[6:12])
    neighbor = _parse_lldpdu(packet[14:])
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
        neighbors = list(_neighbors.values())

    fresh = []
    for neighbor in neighbors:
        age = now - neighbor["last_seen"]
        if age > stale_after:
            continue
        entry = dict(neighbor)
        entry["age_seconds"] = int(age)
        fresh.append(entry)
    fresh.sort(key=lambda n: n["age_seconds"])

    return {"interface": interface, "present": len(fresh) > 0, "neighbors": fresh}
