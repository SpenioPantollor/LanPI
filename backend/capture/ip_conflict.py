"""Passive duplicate-IP (ARP conflict) detection on the TEST PORT
(V0.3 backlog item).

Watches the shared packet-capture dispatcher (backend/capture/
dispatcher.py) for ARP traffic instead of running a separate tcpdump
listener -- same one-capture-many-consumers shape as
traffic_stats.py/modbus_traffic.py/backend/discovery/*.py.

Method: every ARP packet's sender fields (sender IP, sender MAC) are a
claim of "this MAC currently owns this IP" -- true for both requests
("who has X, tell Y", sent by Y about itself) and replies ("X is at
M"), so both opcodes are used, not just replies. Tracks, per IP, every
MAC currently claiming it and when it was last seen; an IP claimed by
more than one MAC at once is a conflict.

Sender IP `0.0.0.0` is explicitly excluded -- that is an ARP *probe*
(RFC 5227 duplicate-address detection, mainly Windows/DHCP clients
checking an address is free before using it), not a claim, and would
otherwise show every probing host colliding with itself.

A MAC's claim on an IP expires after `_MAC_TTL_SECONDS` of not being
reasserted (lazily pruned on read/write, not a separate timer thread).
Without this, a real one-time IP reassignment (a device's DHCP lease
changing hands after it goes offline, a NIC replacement, ...) would
show as a permanent phantom conflict between the old MAC and the new
one long after the old MAC ever spoke again -- an actual conflict
keeps reappearing every time both sides re-ARP, so the TTL doesn't
mask a genuine one.
"""

from __future__ import annotations

import struct
import threading
import time

from backend.capture import dispatcher

_ARP_ETHERTYPE = 0x0806
_MAC_TTL_SECONDS = 600.0  # 10 minutes -- well past any normal ARP refresh interval
_MAX_TRACKED_IPS = 500

_lock = threading.Lock()
_owners: dict[str, dict[str, float]] = {}  # ip -> {mac: last_seen}
_started = False


def _parse_arp(packet: bytes) -> tuple[str, str] | None:
    """Returns (sender_ip, sender_mac), or None if this isn't a
    parseable Ethernet+ARP packet."""
    if len(packet) < 42:
        return None
    ethertype = struct.unpack("!H", packet[12:14])[0]
    if ethertype != _ARP_ETHERTYPE:
        return None
    sender_mac = ":".join(f"{b:02x}" for b in packet[22:28])
    sender_ip = ".".join(str(b) for b in packet[28:32])
    return sender_ip, sender_mac


def _prune_stale_macs(now: float) -> None:
    stale_ips = []
    for ip, macs in _owners.items():
        stale_macs = [mac for mac, last_seen in macs.items() if now - last_seen > _MAC_TTL_SECONDS]
        for mac in stale_macs:
            del macs[mac]
        if not macs:
            stale_ips.append(ip)
    for ip in stale_ips:
        del _owners[ip]


def handle_packet(packet: bytes) -> None:
    parsed = _parse_arp(packet)
    if parsed is None:
        return
    sender_ip, sender_mac = parsed
    if sender_ip == "0.0.0.0":
        return
    now = time.time()

    with _lock:
        _prune_stale_macs(now)
        macs = _owners.get(sender_ip)
        if macs is None:
            if len(_owners) >= _MAX_TRACKED_IPS:
                return
            macs = {}
            _owners[sender_ip] = macs
        macs[sender_mac] = now


def start_listener(interface: str = "eth0") -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    dispatcher.start_listener(interface)
    dispatcher.register_handler(handle_packet)


def get_conflicts() -> dict:
    with _lock:
        _prune_stale_macs(time.time())
        conflicts = []
        for ip, macs in _owners.items():
            if len(macs) < 2:
                continue
            claimants = [{"mac": mac, "last_seen": last_seen} for mac, last_seen in macs.items()]
            claimants.sort(key=lambda c: c["last_seen"], reverse=True)
            conflicts.append({"ip": ip, "macs": claimants})
        conflicts.sort(key=lambda c: max(m["last_seen"] for m in c["macs"]), reverse=True)
        return {"conflicts": conflicts, "tracked_ips": len(_owners)}


def reset() -> dict:
    with _lock:
        _owners.clear()
    return {"ok": True, "message": "IP conflict tracking reset"}
