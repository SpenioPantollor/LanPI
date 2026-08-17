"""Passive traffic statistics on the TEST PORT (eth0), via tcpdump.

Same background-thread-plus-cache shape as backend/discovery/*.py, but
with no BPF filter -- every packet is parsed (Ethernet + IPv4/ARP
headers, plus UDP/TCP ports for a few protocols, no deep payload
inspection) to maintain:

  - running totals: packets/bytes, broadcast/multicast/unicast split,
    per-protocol counts
  - a short rolling window (_RATE_WINDOW_SECONDS) for live packets/s
    and bytes/s rates, both overall and per "talker"
  - per-talker cumulative totals (packets/bytes/broadcast/multicast/
    per-protocol), keyed by source IP when the frame has one (ARP,
    IPv4), falling back to source MAC for L2-only protocols that don't
    (LLDP, CDP, PROFINET real-time frames are non-routable by design)

Top talkers are ranked by *live* bytes/s (from the rolling window),
not lifetime total -- "who's talking right now" is more useful for a
live dashboard than "who talked the most since boot". Cumulative
per-talker fields are still included alongside the live rate.

Runs its own dedicated tcpdump, same as lldp.py/cdp.py/mndp.py do for
their own protocols, rather than fanning a single capture out to
multiple parsers. Unlike those (narrowly filtered) listeners, this one
has no BPF filter at all, so it's a heavier capture on a busy network.

Counters accumulate from when the listener starts until reset() is
called -- no automatic decay or eviction (beyond the talker-count cap
below), which fits a bounded diagnostic session rather than
unattended long-term monitoring.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import threading
import time
from collections import deque

_TCPDUMP_CANDIDATES = ["/usr/bin/tcpdump", "/usr/sbin/tcpdump", "tcpdump"]
_PCAP_GLOBAL_HEADER_LEN = 24
_PCAP_RECORD_HEADER_LEN = 16
_RESTART_DELAY_SECONDS = 5
_RATE_WINDOW_SECONDS = 5.0
_MAX_TALKERS = 500
_CDP_DEST_MAC = b"\x01\x00\x0c\xcc\xcc\xcc"
_BROADCAST_MAC = b"\xff\xff\xff\xff\xff\xff"
_S7_PORT = 102

_PROTOCOL_NAMES = ["arp", "ipv4", "ipv6", "dhcp", "lldp", "cdp", "mdns", "ssdp", "profinet", "s7"]

_lock = threading.Lock()


def _empty_protocol_counts() -> dict:
    return {name: 0 for name in _PROTOCOL_NAMES}


def _empty_stats() -> dict:
    return {
        "started_at": time.time(),
        "packets": 0,
        "bytes": 0,
        "broadcast": 0,
        "multicast": 0,
        "unicast": 0,
        "protocols": _empty_protocol_counts(),
    }


_stats = _empty_stats()
_talkers: dict[str, dict] = {}
_recent_packets: deque = deque()  # (timestamp, identity, length)
_started_interfaces: set[str] = set()


def _find_tcpdump() -> str | None:
    for candidate in _TCPDUMP_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _classify(packet: bytes) -> tuple[str, bool, bool, frozenset]:
    """Returns (identity, is_broadcast, is_multicast, protocols)."""
    n = len(packet)
    dst_mac = packet[0:6]
    src_mac = packet[6:12]
    ethertype = struct.unpack("!H", packet[12:14])[0]

    is_broadcast = dst_mac == _BROADCAST_MAC
    is_multicast = (not is_broadcast) and bool(dst_mac[0] & 0x01)

    protocols: set[str] = set()
    identity = ":".join(f"{b:02x}" for b in src_mac)  # fallback for L2-only frames

    if ethertype == 0x0806:  # ARP
        protocols.add("arp")
        if n >= 32:
            identity = ".".join(str(b) for b in packet[28:32])
    elif ethertype == 0x0800:  # IPv4
        protocols.add("ipv4")
        if n >= 34:
            ihl = (packet[14] & 0x0F) * 4
            proto = packet[23]
            identity = ".".join(str(b) for b in packet[26:30])
            l4_offset = 14 + ihl
            if proto == 17 and n >= l4_offset + 4:  # UDP
                sport, dport = struct.unpack("!HH", packet[l4_offset:l4_offset + 4])
                if 67 in (sport, dport) or 68 in (sport, dport):
                    protocols.add("dhcp")
                elif sport == 5353 or dport == 5353:
                    protocols.add("mdns")
                elif sport == 1900 or dport == 1900:
                    protocols.add("ssdp")
            elif proto == 6 and n >= l4_offset + 4:  # TCP
                sport, dport = struct.unpack("!HH", packet[l4_offset:l4_offset + 4])
                if sport == _S7_PORT or dport == _S7_PORT:
                    protocols.add("s7")
    elif ethertype == 0x86DD:  # IPv6
        protocols.add("ipv6")
    elif ethertype == 0x88CC:  # LLDP
        protocols.add("lldp")
    elif ethertype == 0x8892:  # PROFINET (RT + DCP share this EtherType)
        protocols.add("profinet")
    elif dst_mac == _CDP_DEST_MAC:
        protocols.add("cdp")

    return identity, is_broadcast, is_multicast, frozenset(protocols)


def _classify_and_record(packet: bytes) -> None:
    if len(packet) < 14:
        return
    length = len(packet)
    identity, is_broadcast, is_multicast, protocols = _classify(packet)
    now = time.time()

    with _lock:
        _stats["packets"] += 1
        _stats["bytes"] += length
        if is_broadcast:
            _stats["broadcast"] += 1
        elif is_multicast:
            _stats["multicast"] += 1
        else:
            _stats["unicast"] += 1
        for p in protocols:
            _stats["protocols"][p] += 1

        talker = _talkers.get(identity)
        if talker is None and len(_talkers) < _MAX_TALKERS:
            talker = {
                "packets": 0, "bytes": 0, "broadcast": 0, "multicast": 0,
                "protocols": _empty_protocol_counts(),
            }
            _talkers[identity] = talker
        if talker is not None:
            talker["packets"] += 1
            talker["bytes"] += length
            if is_broadcast:
                talker["broadcast"] += 1
            elif is_multicast:
                talker["multicast"] += 1
            for p in protocols:
                talker["protocols"][p] += 1

        _recent_packets.append((now, identity, length))
        cutoff = now - _RATE_WINDOW_SECONDS
        while _recent_packets and _recent_packets[0][0] < cutoff:
            _recent_packets.popleft()


def _read_exact(stream, count: int) -> bytes | None:
    data = b""
    while len(data) < count:
        chunk = stream.read(count - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def _capture_loop(interface: str) -> None:
    tcpdump = _find_tcpdump()
    if not tcpdump:
        return

    while True:
        proc = None
        try:
            proc = subprocess.Popen(
                [tcpdump, "-i", interface, "-U", "-nn", "-w", "-"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            stdout = proc.stdout
            if _read_exact(stdout, _PCAP_GLOBAL_HEADER_LEN) is None:
                continue

            while True:
                record_header = _read_exact(stdout, _PCAP_RECORD_HEADER_LEN)
                if record_header is None:
                    break
                _, _, incl_len, _ = struct.unpack("<IIII", record_header)
                packet = _read_exact(stdout, incl_len)
                if packet is None:
                    break
                _classify_and_record(packet)
        except Exception:
            pass
        finally:
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    pass
        time.sleep(_RESTART_DELAY_SECONDS)


def start_listener(interface: str = "eth0") -> None:
    with _lock:
        if interface in _started_interfaces:
            return
        _started_interfaces.add(interface)
    thread = threading.Thread(target=_capture_loop, args=(interface,), daemon=True)
    thread.start()


def get_stats(top_n: int = 15) -> dict:
    with _lock:
        now = time.time()
        cutoff = now - _RATE_WINDOW_SECONDS
        while _recent_packets and _recent_packets[0][0] < cutoff:
            _recent_packets.popleft()

        recent_by_identity: dict[str, dict] = {}
        recent_bytes_total = 0
        for _, identity, length in _recent_packets:
            entry = recent_by_identity.setdefault(identity, {"packets": 0, "bytes": 0})
            entry["packets"] += 1
            entry["bytes"] += length
            recent_bytes_total += length

        elapsed = now - _stats["started_at"]

        talkers_list = []
        for identity, cum in _talkers.items():
            recent = recent_by_identity.get(identity, {"packets": 0, "bytes": 0})
            talkers_list.append(
                {
                    "identity": identity,
                    "packets_per_second": round(recent["packets"] / _RATE_WINDOW_SECONDS, 2),
                    "bytes_per_second": round(recent["bytes"] / _RATE_WINDOW_SECONDS, 2),
                    "packets": cum["packets"],
                    "bytes": cum["bytes"],
                    "broadcast": cum["broadcast"],
                    "multicast": cum["multicast"],
                    "protocols": dict(cum["protocols"]),
                }
            )
        talkers_list.sort(key=lambda t: t["bytes_per_second"], reverse=True)

        return {
            "elapsed_seconds": round(elapsed, 1),
            "packets": _stats["packets"],
            "bytes": _stats["bytes"],
            "packets_per_second": round(len(_recent_packets) / _RATE_WINDOW_SECONDS, 2),
            "bytes_per_second": round(recent_bytes_total / _RATE_WINDOW_SECONDS, 2),
            "broadcast": _stats["broadcast"],
            "multicast": _stats["multicast"],
            "unicast": _stats["unicast"],
            "protocols": dict(_stats["protocols"]),
            "top_talkers": talkers_list[:top_n],
        }


def reset() -> dict:
    with _lock:
        _stats.clear()
        _stats.update(_empty_stats())
        _talkers.clear()
        _recent_packets.clear()
    return {"ok": True, "message": "traffic stats reset"}
