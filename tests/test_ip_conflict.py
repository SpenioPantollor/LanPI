"""Tests for backend/capture/ip_conflict.py's ARP-based duplicate-IP
detection.

Feeds hand-built raw Ethernet+ARP frames straight to handle_packet(),
the same shape the dispatcher hands every listener after stripping
pcap record framing -- no real capture involved.
"""
import struct
import time

import pytest

from backend.capture import ip_conflict


def _mac_bytes(s: str) -> bytes:
    return bytes(int(b, 16) for b in s.split(":"))


def _ip_bytes(ip: str) -> bytes:
    return bytes(int(o) for o in ip.split("."))


def _arp_packet(sender_ip: str, sender_mac: str, opcode: int = 1, target_ip: str = "0.0.0.0") -> bytes:
    eth = _mac_bytes("ff:ff:ff:ff:ff:ff") + _mac_bytes(sender_mac) + struct.pack("!H", 0x0806)
    arp = bytearray(28)
    struct.pack_into("!HHBBH", arp, 0, 1, 0x0800, 6, 4, opcode)
    arp[8:14] = _mac_bytes(sender_mac)
    arp[14:18] = _ip_bytes(sender_ip)
    arp[18:24] = _mac_bytes("00:00:00:00:00:00")
    arp[24:28] = _ip_bytes(target_ip)
    return bytes(eth) + bytes(arp)


@pytest.fixture(autouse=True)
def _reset_state():
    ip_conflict.reset()
    yield
    ip_conflict.reset()


def test_ignores_non_arp_traffic():
    packet = b"\x00" * 14 + b"not arp"
    ip_conflict.handle_packet(packet)
    assert ip_conflict.get_conflicts()["conflicts"] == []


def test_single_claimant_is_not_a_conflict():
    ip_conflict.handle_packet(_arp_packet("10.0.0.5", "aa:aa:aa:aa:aa:aa"))
    assert ip_conflict.get_conflicts()["conflicts"] == []
    assert ip_conflict.get_conflicts()["tracked_ips"] == 1


def test_two_macs_claiming_the_same_ip_is_a_conflict():
    ip_conflict.handle_packet(_arp_packet("10.0.0.5", "aa:aa:aa:aa:aa:aa"))
    ip_conflict.handle_packet(_arp_packet("10.0.0.5", "bb:bb:bb:bb:bb:bb", opcode=2))

    conflicts = ip_conflict.get_conflicts()["conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["ip"] == "10.0.0.5"
    macs = {m["mac"] for m in conflicts[0]["macs"]}
    assert macs == {"aa:aa:aa:aa:aa:aa", "bb:bb:bb:bb:bb:bb"}


def test_arp_probe_from_0000_is_not_tracked():
    ip_conflict.handle_packet(_arp_packet("0.0.0.0", "aa:aa:aa:aa:aa:aa", target_ip="10.0.0.5"))
    assert ip_conflict.get_conflicts()["tracked_ips"] == 0


def test_different_ips_are_not_a_conflict():
    ip_conflict.handle_packet(_arp_packet("10.0.0.5", "aa:aa:aa:aa:aa:aa"))
    ip_conflict.handle_packet(_arp_packet("10.0.0.6", "bb:bb:bb:bb:bb:bb"))
    assert ip_conflict.get_conflicts()["conflicts"] == []
    assert ip_conflict.get_conflicts()["tracked_ips"] == 2


def test_stale_mac_is_pruned_and_conflict_clears(monkeypatch):
    monkeypatch.setattr(ip_conflict, "_MAC_TTL_SECONDS", 0.01)
    ip_conflict.handle_packet(_arp_packet("10.0.0.5", "aa:aa:aa:aa:aa:aa"))
    ip_conflict.handle_packet(_arp_packet("10.0.0.5", "bb:bb:bb:bb:bb:bb", opcode=2))
    assert len(ip_conflict.get_conflicts()["conflicts"]) == 1

    time.sleep(0.05)

    assert ip_conflict.get_conflicts()["conflicts"] == []
    assert ip_conflict.get_conflicts()["tracked_ips"] == 0


def test_reset_clears_tracked_owners():
    ip_conflict.handle_packet(_arp_packet("10.0.0.5", "aa:aa:aa:aa:aa:aa"))
    ip_conflict.reset()
    assert ip_conflict.get_conflicts() == {"conflicts": [], "tracked_ips": 0}
