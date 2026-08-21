"""Tests for backend/discovery/cdp.py's pure TLV parser (_parse_cdp_payload)
and its address-TLV sub-parser (_parse_address_tlv)."""
import struct

from backend.discovery import cdp


def _tlv(tlv_type: int, value: bytes) -> bytes:
    length = 4 + len(value)  # CDP TLV length includes its own 4-byte header
    return struct.pack("!HH", tlv_type, length) + value


def _address_tlv_value(addresses: list[bytes]) -> bytes:
    """One IPv4 address entry per item in `addresses` (each 4 raw bytes)."""
    out = struct.pack("!I", len(addresses))
    for addr in addresses:
        out += bytes([1, 1])  # protocol_type=1 (NLPID), protocol_length=1
        out += b"\xcc"  # protocol = IP
        out += struct.pack("!H", len(addr))
        out += addr
    return out


def _cdp_payload(tlvs: bytes) -> bytes:
    return b"\x02\xb4\x00\x00" + tlvs  # version(1) ttl(1) checksum(2)


def test_parses_device_id_port_id_platform():
    payload = _cdp_payload(
        _tlv(0x0001, b"switch01.example.com")
        + _tlv(0x0003, b"GigabitEthernet0/1")
        + _tlv(0x0006, b"cisco WS-C2960-24TT-L")
    )
    neighbor = cdp._parse_cdp_payload(payload)
    assert neighbor["device_id"] == "switch01.example.com"
    assert neighbor["port_id"] == "GigabitEthernet0/1"
    assert neighbor["platform"] == "cisco WS-C2960-24TT-L"


def test_software_version_takes_only_first_line():
    payload = _cdp_payload(_tlv(0x0005, b"Cisco IOS Software\nVersion 15.2(4)E"))
    neighbor = cdp._parse_cdp_payload(payload)
    assert neighbor["software_version"] == "Cisco IOS Software"


def test_native_vlan():
    payload = _cdp_payload(_tlv(0x000a, struct.pack("!H", 100)))
    neighbor = cdp._parse_cdp_payload(payload)
    assert neighbor["native_vlan"] == 100


def test_address_tlv_extracts_first_ipv4():
    payload = _cdp_payload(_tlv(0x0002, _address_tlv_value([bytes([10, 0, 0, 1])])))
    neighbor = cdp._parse_cdp_payload(payload)
    assert neighbor["address"] == "10.0.0.1"


def test_management_address_tlv_also_extracts_ipv4():
    payload = _cdp_payload(_tlv(0x0016, _address_tlv_value([bytes([172, 16, 0, 5])])))
    neighbor = cdp._parse_cdp_payload(payload)
    assert neighbor["address"] == "172.16.0.5"


def test_too_short_payload_returns_all_none():
    neighbor = cdp._parse_cdp_payload(b"\x02")
    assert all(v is None for v in neighbor.values())


def test_address_tlv_with_zero_addresses_returns_none():
    assert cdp._parse_address_tlv(struct.pack("!I", 0)) is None
