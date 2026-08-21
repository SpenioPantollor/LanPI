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
    per-protocol), keyed by source MAC -- MAC is always present at L2,
    unlike IP (LLDP/CDP/PROFINET real-time frames are non-routable and
    have none). The most recently seen source IP for that MAC, if any,
    is attached to the same entry rather than creating a second one --
    a device sending both IPv4 and LLDP traffic used to show up as two
    unrelated rows (one by IP, one by MAC) before this (user-reported).

Top talkers are ranked by *live* bytes/s (from the rolling window),
not lifetime total -- "who's talking right now" is more useful for a
live dashboard than "who talked the most since boot". Cumulative
per-talker fields are still included alongside the live rate.

The TEST PORT interface's own MAC is excluded from top_talkers (but
still counted in the overall totals) -- LanPi's own tools generate
real traffic on eth0 (ping, tcp-test, mtr, arp-scan, ip-scan,
port-scan, this capture's own DHCP renewals, ...), which isn't a
"neighbor on the network" the way every other talker is (user-reported
noise).

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
_SELF_MAC_PATH = "/sys/class/net/eth0/address"

_PROTOCOL_NAMES = ["arp", "ipv4", "ipv6", "dhcp", "lldp", "cdp", "mdns", "ssdp", "profinet", "s7"]

_lock = threading.Lock()
_self_mac_cache: str | None = None
_self_mac_checked = False


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
_talkers: dict[str, dict] = {}  # mac -> {ip, packets, bytes, broadcast, multicast, protocols}
_recent_packets: deque = deque()  # (timestamp, mac, length)
_started_interfaces: set[str] = set()


def _find_tcpdump() -> str | None:
    for candidate in _TCPDUMP_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _self_mac() -> str | None:
    """The TEST PORT's own MAC, cached -- read once, not on every
    packet, since this is on the hot path of every captured frame."""
    global _self_mac_cache, _self_mac_checked
    if not _self_mac_checked:
        _self_mac_checked = True
        try:
            with open(_SELF_MAC_PATH) as f:
                _self_mac_cache = f.read().strip().lower() or None
        except OSError:
            _self_mac_cache = None
    return _self_mac_cache


def _classify(packet: bytes) -> tuple[str, str | None, bool, bool, frozenset]:
    """Returns (src_mac, src_ip_or_None, is_broadcast, is_multicast, protocols)."""
    n = len(packet)
    dst_mac = packet[0:6]
    src_mac = ":".join(f"{b:02x}" for b in packet[6:12])
    ethertype = struct.unpack("!H", packet[12:14])[0]

    is_broadcast = dst_mac == _BROADCAST_MAC
    is_multicast = (not is_broadcast) and bool(dst_mac[0] & 0x01)

    protocols: set[str] = set()
    src_ip: str | None = None

    if ethertype == 0x0806:  # ARP
        protocols.add("arp")
        if n >= 32:
            src_ip = ".".join(str(b) for b in packet[28:32])
    elif ethertype == 0x0800:  # IPv4
        protocols.add("ipv4")
        if n >= 34:
            ihl = (packet[14] & 0x0F) * 4
            proto = packet[23]
            src_ip = ".".join(str(b) for b in packet[26:30])
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

    return src_mac, src_ip, is_broadcast, is_multicast, frozenset(protocols)


def _classify_and_record(packet: bytes) -> None:
    if len(packet) < 14:
        return
    length = len(packet)
    src_mac, src_ip, is_broadcast, is_multicast, protocols = _classify(packet)
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

        # Overall rate (packets/bytes per second) includes everything,
        # LanPi's own traffic included -- only the per-talker table
        # below excludes it.
        _recent_packets.append((now, src_mac, length))
        cutoff = now - _RATE_WINDOW_SECONDS
        while _recent_packets and _recent_packets[0][0] < cutoff:
            _recent_packets.popleft()

        if src_mac == _self_mac():
            return

        talker = _talkers.get(src_mac)
        if talker is None and len(_talkers) < _MAX_TALKERS:
            talker = {
                "ip": None, "packets": 0, "bytes": 0, "broadcast": 0, "multicast": 0,
                "protocols": _empty_protocol_counts(),
            }
            _talkers[src_mac] = talker
        if talker is not None:
            talker["packets"] += 1
            talker["bytes"] += length
            if src_ip:
                talker["ip"] = src_ip
            if is_broadcast:
                talker["broadcast"] += 1
            elif is_multicast:
                talker["multicast"] += 1
            for p in protocols:
                talker["protocols"][p] += 1


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


def get_stats() -> dict:
    with _lock:
        now = time.time()
        cutoff = now - _RATE_WINDOW_SECONDS
        while _recent_packets and _recent_packets[0][0] < cutoff:
            _recent_packets.popleft()

        recent_by_mac: dict[str, dict] = {}
        recent_bytes_total = 0
        for _, mac, length in _recent_packets:
            entry = recent_by_mac.setdefault(mac, {"packets": 0, "bytes": 0})
            entry["packets"] += 1
            entry["bytes"] += length
            recent_bytes_total += length

        elapsed = now - _stats["started_at"]

        talkers_list = []
        for mac, cum in _talkers.items():
            recent = recent_by_mac.get(mac, {"packets": 0, "bytes": 0})
            talkers_list.append(
                {
                    "mac": mac,
                    "ip": cum["ip"],
                    "packets_per_second": round(recent["packets"] / _RATE_WINDOW_SECONDS, 2),
                    "bytes_per_second": round(recent["bytes"] / _RATE_WINDOW_SECONDS, 2),
                    "packets": cum["packets"],
                    "bytes": cum["bytes"],
                    "broadcast": cum["broadcast"],
                    "multicast": cum["multicast"],
                    "protocols": dict(cum["protocols"]),
                }
            )
        # Default/API order ranks by cumulative bytes over the whole
        # summary period (since start/reset), not the live 5s rate --
        # matches the Summary card above it, which is also cumulative.
        # The frontend re-sorts client-side on column click; every
        # talker is returned (not just a top-N slice) so sorting by
        # any other column is still accurate, not limited to whichever
        # subset happened to be the top N by bytes.
        talkers_list.sort(key=lambda t: t["bytes"], reverse=True)

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
            "top_talkers": talkers_list,
        }


def reset() -> dict:
    with _lock:
        _stats.clear()
        _stats.update(_empty_stats())
        _talkers.clear()
        _recent_packets.clear()
    return {"ok": True, "message": "traffic stats reset"}
