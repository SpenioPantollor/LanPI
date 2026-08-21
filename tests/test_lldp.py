"""Tests for backend/discovery/lldp.py: the pure TLV parser
(_parse_lldpdu) and _handle_packet(), the dispatcher-facing entry point
that does the EtherType filter formerly done by a BPF filter at the
tcpdump level (see backend/capture/dispatcher.py).

No tcpdump/subprocess/network involved -- these feed hand-built raw
Ethernet frames / LLDPDU byte sequences straight to the module, the
same shape the dispatcher hands every listener after stripping pcap
record framing.
"""
import struct

from backend.discovery import lldp


def _tlv(tlv_type: int, value: bytes) -> bytes:
    header = (tlv_type << 9) | len(value)
    return struct.pack("!H", header) + value


def _eth_frame(ethertype: int, payload: bytes) -> bytes:
    return b"\x00" * 6 + b"\x11" * 6 + struct.pack("!H", ethertype) + payload


def _mac(s: str) -> bytes:
    return bytes(int(b, 16) for b in s.split(":"))


def test_parses_mac_chassis_and_port_id():
    payload = (
        _tlv(1, b"\x04" + _mac("aa:bb:cc:dd:ee:ff"))  # chassis ID, MAC subtype
        + _tlv(2, b"\x03" + _mac("11:22:33:44:55:66"))  # port ID, MAC subtype
        + _tlv(0, b"")  # end of LLDPDU
    )
    neighbor = lldp._parse_lldpdu(payload)
    assert neighbor["chassis_id"] == "aa:bb:cc:dd:ee:ff"
    assert neighbor["port_id"] == "11:22:33:44:55:66"


def test_parses_string_chassis_id_for_non_mac_subtype():
    payload = _tlv(1, b"\x07switch-01")  # subtype 7: locally assigned
    neighbor = lldp._parse_lldpdu(payload)
    assert neighbor["chassis_id"] == "switch-01"


def test_parses_names_descriptions_and_vlan():
    payload = (
        _tlv(4, b"GigabitEthernet0/1")
        + _tlv(5, b"switch01")
        + _tlv(6, b"Cisco IOS  ")
        + _tlv(127, b"\x00\x80\xc2" + b"\x03" + struct.pack("!H", 42))  # 802.1 port VLAN
    )
    neighbor = lldp._parse_lldpdu(payload)
    assert neighbor["port_description"] == "GigabitEthernet0/1"
    assert neighbor["system_name"] == "switch01"
    assert neighbor["system_description"] == "Cisco IOS"  # stripped
    assert neighbor["vlan"] == 42


def test_parses_ipv4_management_address():
    addr_value = bytes([5, 1]) + bytes([192, 168, 1, 1])  # addr_len=5 (subtype+4), subtype=1 (IPv4)
    payload = _tlv(8, addr_value)
    neighbor = lldp._parse_lldpdu(payload)
    assert neighbor["management_ip"] == "192.168.1.1"


def test_ignores_unknown_tlv_types():
    payload = _tlv(99, b"unrecognized")
    neighbor = lldp._parse_lldpdu(payload)
    assert all(v is None for v in neighbor.values())


def test_truncated_tlv_stops_without_raising():
    # Header claims a 10-byte value but only 2 bytes follow.
    header = struct.pack("!H", (1 << 9) | 10)
    payload = header + b"\x04\xaa"
    neighbor = lldp._parse_lldpdu(payload)
    assert neighbor["chassis_id"] is None


def test_empty_payload_returns_all_none():
    neighbor = lldp._parse_lldpdu(b"")
    assert all(v is None for v in neighbor.values())


def test_handle_packet_ignores_non_lldp_ethertype():
    lldp._neighbors.clear()
    packet = _eth_frame(0x0800, b"\x00" * 20)  # IPv4, not LLDP

    lldp._handle_packet("eth0", packet)

    assert lldp._neighbors == {}


def test_handle_packet_parses_and_caches_an_lldp_frame():
    lldp._neighbors.clear()
    packet = _eth_frame(0x88CC, _tlv(5, b"switch01"))

    lldp._handle_packet("eth0", packet)

    assert lldp._neighbors["eth0"]["system_name"] == "switch01"
