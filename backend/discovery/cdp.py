"""Passive CDP (Cisco Discovery Protocol) neighbor discovery.

Packets arrive via backend/capture/dispatcher.py's single shared
tcpdump capture (v0.2.3 Foundation #3) rather than this module running
its own dedicated tcpdump process -- see that module's docstring for
why. _handle_packet() does the CDP dest-MAC + LLC/SNAP filter that
used to be a BPF filter at the tcpdump level, then hands off to the
same _parse_cdp_payload() TLV parser as before.

CDP frames are 802.3 + LLC/SNAP (not a plain EtherType frame like
LLDP), so the framing/parsing differs even though the overall approach
doesn't:

  [Ethernet: dst(6) src(6) len(2)] [LLC: dsap ssap ctrl] [SNAP: oui(3) pid(2)] [CDP payload]

CDP payload: version(1) ttl(1) checksum(2) then TLVs (type(2) length(2,
includes this 4-byte header) value(length-4)).

Caches every distinct neighbor seen, keyed by source MAC, not just the
single most-recently-seen one -- same fix and reasoning as
backend/discovery/lldp.py (2026-08-25): a single-neighbor cache would
flicker/overwrite itself the moment more than one CDP-speaking device
was reachable through the test switch. Same shape as
traffic_stats.py's _talkers dict, same bounding (_MAX_NEIGHBORS,
oldest-by-last-seen evicted over the cap).
"""

from __future__ import annotations

import struct
import threading
import time

from backend.capture import dispatcher

_CDP_DEST_MAC = b"\x01\x00\x0c\xcc\xcc\xcc"
_CDP_SNAP_OUI = b"\x00\x00\x0c"
_CDP_SNAP_PID = b"\x20\x00"
_MAX_NEIGHBORS = 500
_DEFAULT_STALE_AFTER = 60.0  # user-set: purge a neighbor entirely after 60s of silence

_lock = threading.Lock()
_neighbors: dict[str, dict] = {}  # mac -> {..fields.., mac, last_seen}
_started_interfaces: set[str] = set()


def _parse_address_tlv(value: bytes) -> str | None:
    """First IPv4 address in a CDP Address(es)/Management-Address(es) TLV."""
    if len(value) < 4:
        return None
    try:
        count = struct.unpack("!I", value[0:4])[0]
    except struct.error:
        return None

    offset = 4
    for _ in range(count):
        if offset + 2 > len(value):
            break
        protocol_type = value[offset]
        protocol_length = value[offset + 1]
        offset += 2
        protocol = value[offset:offset + protocol_length]
        offset += protocol_length
        if offset + 2 > len(value):
            break
        address_length = struct.unpack("!H", value[offset:offset + 2])[0]
        offset += 2
        address = value[offset:offset + address_length]
        offset += address_length

        if protocol_type == 1 and protocol == b"\xcc" and address_length == 4:
            return ".".join(str(b) for b in address)

    return None


def _parse_cdp_payload(payload: bytes) -> dict:
    neighbor = {
        "device_id": None,
        "port_id": None,
        "platform": None,
        "software_version": None,
        "native_vlan": None,
        "address": None,
    }
    if len(payload) < 4:
        return neighbor

    i = 4  # skip version(1) + ttl(1) + checksum(2)
    n = len(payload)
    while i + 4 <= n:
        tlv_type, tlv_len = struct.unpack("!HH", payload[i:i + 4])
        value = payload[i + 4:i + tlv_len]
        if len(value) < tlv_len - 4:
            break
        i += tlv_len if tlv_len >= 4 else n  # malformed length: bail

        if tlv_type == 0x0001:
            neighbor["device_id"] = value.decode("utf-8", errors="replace")
        elif tlv_type == 0x0002:
            addr = _parse_address_tlv(value)
            if addr:
                neighbor["address"] = addr
        elif tlv_type == 0x0003:
            neighbor["port_id"] = value.decode("utf-8", errors="replace")
        elif tlv_type == 0x0005:
            neighbor["software_version"] = value.decode("utf-8", errors="replace").splitlines()[0].strip()
        elif tlv_type == 0x0006:
            neighbor["platform"] = value.decode("utf-8", errors="replace")
        elif tlv_type == 0x000a and len(value) >= 2:
            neighbor["native_vlan"] = struct.unpack("!H", value[0:2])[0]
        elif tlv_type == 0x0016:
            addr = _parse_address_tlv(value)
            if addr:
                neighbor["address"] = addr

    return neighbor


def _handle_packet(interface: str, packet: bytes) -> None:
    if len(packet) < 22:
        return
    if packet[0:6] != _CDP_DEST_MAC:
        return

    dsap, ssap, control = packet[14], packet[15], packet[16]
    if dsap != 0xAA or ssap != 0xAA or control != 0x03:
        return
    oui = packet[17:20]
    pid = packet[20:22]
    if oui != _CDP_SNAP_OUI or pid != _CDP_SNAP_PID:
        return

    mac = ":".join(f"{b:02x}" for b in packet[6:12])
    neighbor = _parse_cdp_payload(packet[22:])
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
