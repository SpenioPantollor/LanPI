"""Tests for backend/discovery/mndp.py's pure TLV parser (_parse_mndp_payload)."""
import struct

from backend.discovery import mndp


def _tlv(tlv_type: int, value: bytes) -> bytes:
    return struct.pack("!HH", tlv_type, len(value)) + value


def _mac(s: str) -> bytes:
    return bytes(int(b, 16) for b in s.split(":"))


def _mndp_payload(tlvs: bytes) -> bytes:
    return b"\x00\x00\x00\x01" + tlvs  # 2-byte header + 2-byte sequence number


def test_parses_mac_identity_and_versions():
    payload = _mndp_payload(
        _tlv(0x0001, _mac("aa:bb:cc:dd:ee:ff"))
        + _tlv(0x0005, b"office-router")
        + _tlv(0x0007, b"7.15.3")
        + _tlv(0x0008, b"MikroTik")
    )
    neighbor = mndp._parse_mndp_payload(payload)
    assert neighbor["mac_address"] == "aa:bb:cc:dd:ee:ff"
    assert neighbor["identity"] == "office-router"
    assert neighbor["version"] == "7.15.3"
    assert neighbor["platform"] == "MikroTik"


def test_uptime_is_little_endian():
    payload = _mndp_payload(_tlv(0x000A, struct.pack("<I", 3661)))
    neighbor = mndp._parse_mndp_payload(payload)
    assert neighbor["uptime_seconds"] == 3661


def test_software_id_board_and_interface():
    payload = _mndp_payload(
        _tlv(0x000B, b"9GX8")
        + _tlv(0x000C, b"RB750Gr3")
        + _tlv(0x0010, b"ether1")
    )
    neighbor = mndp._parse_mndp_payload(payload)
    assert neighbor["software_id"] == "9GX8"
    assert neighbor["board"] == "RB750Gr3"
    assert neighbor["interface_name"] == "ether1"


def test_ipv4_address():
    payload = _mndp_payload(_tlv(0x0011, bytes([192, 168, 88, 1])))
    neighbor = mndp._parse_mndp_payload(payload)
    assert neighbor["ipv4_address"] == "192.168.88.1"


def test_too_short_payload_returns_all_none():
    neighbor = mndp._parse_mndp_payload(b"\x00\x00")
    assert all(v is None for v in neighbor.values())
