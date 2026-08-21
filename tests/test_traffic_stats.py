"""Tests for backend/capture/traffic_stats.py's packet classifier and the
merge/self-exclusion logic added this session (user-reported: duplicate
rows for one device sending both IPv4 and LLDP, and the Pi's own eth0
traffic cluttering Top Talkers).

Feeds hand-built raw Ethernet frames straight to _classify() /
_classify_and_record(), the same shape tcpdump would hand them after
pcap-record framing is stripped -- no real capture involved.
"""
import struct

import pytest

from backend.capture import traffic_stats

_BROADCAST = b"\xff\xff\xff\xff\xff\xff"
_CDP_DEST = b"\x01\x00\x0c\xcc\xcc\xcc"


def _mac(s: str) -> bytes:
    return bytes(int(b, 16) for b in s.split(":"))


def _eth(dst: bytes, src: bytes, ethertype: int, payload: bytes = b"") -> bytes:
    return dst + src + struct.pack("!H", ethertype) + payload


def _arp_packet(src_mac: str, sender_ip: str, dst=_BROADCAST) -> bytes:
    arp = (
        struct.pack("!HHBBH", 1, 0x0800, 6, 4, 1)  # hw/proto type+len, opcode=request
        + _mac(src_mac)
        + bytes(int(o) for o in sender_ip.split("."))
        + b"\x00" * 6
        + b"\x00\x00\x00\x00"
    )
    return _eth(dst, _mac(src_mac), 0x0806, arp)


def _ipv4_udp_packet(src_mac: str, src_ip: str, sport: int, dport: int, dst=_BROADCAST) -> bytes:
    ip_header = bytearray(20)
    ip_header[0] = 0x45  # version 4, IHL 5 (20 bytes)
    ip_header[9] = 17  # UDP
    ip_header[12:16] = bytes(int(o) for o in src_ip.split("."))
    ip_header[16:20] = bytes([0, 0, 0, 0])
    udp_header = struct.pack("!HHHH", sport, dport, 8, 0)
    return _eth(dst, _mac(src_mac), 0x0800, bytes(ip_header) + udp_header)


def _lldp_packet(src_mac: str, dst=_mac("01:80:c2:00:00:0e")) -> bytes:
    return _eth(dst, _mac(src_mac), 0x88CC, b"\x00" * 4)


def _cdp_packet(src_mac: str) -> bytes:
    # CDP is identified by destination MAC, not EtherType -- use an
    # 802.3 length field (< 1536) so it doesn't collide with any of the
    # EtherType branches checked first.
    return _eth(_CDP_DEST, _mac(src_mac), 0x0026, b"\x00" * 8)


@pytest.fixture(autouse=True)
def _reset_state():
    traffic_stats.reset()
    yield
    traffic_stats.reset()


def test_classify_arp_broadcast():
    packet = _arp_packet("aa:bb:cc:dd:ee:01", "10.0.0.5")
    src_mac, src_ip, is_broadcast, is_multicast, protocols = traffic_stats._classify(packet)
    assert src_mac == "aa:bb:cc:dd:ee:01"
    assert src_ip == "10.0.0.5"
    assert is_broadcast is True
    assert is_multicast is False
    assert protocols == frozenset({"arp"})


def test_classify_dhcp_by_port():
    packet = _ipv4_udp_packet("aa:bb:cc:dd:ee:02", "10.0.0.6", sport=68, dport=67)
    _, src_ip, _, _, protocols = traffic_stats._classify(packet)
    assert src_ip == "10.0.0.6"
    assert "ipv4" in protocols
    assert "dhcp" in protocols


def test_classify_mdns_by_port():
    packet = _ipv4_udp_packet("aa:bb:cc:dd:ee:03", "10.0.0.7", sport=5353, dport=5353)
    _, _, _, _, protocols = traffic_stats._classify(packet)
    assert "mdns" in protocols


def test_classify_lldp_multicast():
    packet = _lldp_packet("aa:bb:cc:dd:ee:04")
    _, _, is_broadcast, is_multicast, protocols = traffic_stats._classify(packet)
    assert is_broadcast is False
    assert is_multicast is True  # LLDP's multicast dst has the low bit of the first octet set
    assert protocols == frozenset({"lldp"})


def test_classify_cdp_by_dest_mac():
    packet = _cdp_packet("aa:bb:cc:dd:ee:05")
    _, _, _, _, protocols = traffic_stats._classify(packet)
    assert protocols == frozenset({"cdp"})


def test_merges_ip_and_lldp_traffic_from_same_mac_into_one_talker():
    mac = "aa:bb:cc:dd:ee:06"
    traffic_stats._classify_and_record(_ipv4_udp_packet(mac, "10.0.0.8", 68, 67))
    traffic_stats._classify_and_record(_lldp_packet(mac))

    talkers = traffic_stats.get_stats()["top_talkers"]
    assert len(talkers) == 1
    talker = talkers[0]
    assert talker["mac"] == mac
    assert talker["ip"] == "10.0.0.8"  # kept from the IPv4 packet
    assert talker["packets"] == 2
    assert talker["protocols"]["dhcp"] == 1
    assert talker["protocols"]["lldp"] == 1


def test_self_mac_excluded_from_talkers_but_counted_in_totals(monkeypatch):
    self_mac = "de:ad:be:ef:00:01"
    monkeypatch.setattr(traffic_stats, "_self_mac", lambda: self_mac)

    traffic_stats._classify_and_record(_arp_packet(self_mac, "10.0.0.9"))
    stats = traffic_stats.get_stats()

    assert stats["packets"] == 1  # still counted overall
    assert stats["top_talkers"] == []  # but excluded from the per-talker table


def test_top_talkers_ranked_by_cumulative_bytes_not_live_rate():
    small_mac, big_mac = "aa:bb:cc:dd:ee:07", "aa:bb:cc:dd:ee:08"
    small_packet = _arp_packet(small_mac, "10.0.0.10")
    big_packet = _arp_packet(big_mac, "10.0.0.11") + b"\x00" * 100  # padded, more bytes

    traffic_stats._classify_and_record(small_packet)
    traffic_stats._classify_and_record(big_packet)

    talkers = traffic_stats.get_stats()["top_talkers"]
    assert [t["mac"] for t in talkers] == [big_mac, small_mac]


def test_reset_clears_totals_and_talkers():
    traffic_stats._classify_and_record(_arp_packet("aa:bb:cc:dd:ee:09", "10.0.0.12"))
    traffic_stats.reset()
    stats = traffic_stats.get_stats()
    assert stats["packets"] == 0
    assert stats["top_talkers"] == []
