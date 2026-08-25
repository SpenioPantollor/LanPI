"""Tests for backend/discovery/cdp.py: the pure TLV parser
(_parse_cdp_payload), its address-TLV sub-parser (_parse_address_tlv),
and _handle_packet(), the dispatcher-facing entry point that does the
dest-MAC + LLC/SNAP filter formerly done by a BPF filter at the
tcpdump level (see backend/capture/dispatcher.py)."""
import struct

from backend.discovery import cdp


def _tlv(tlv_type: int, value: bytes) -> bytes:
    length = 4 + len(value)  # CDP TLV length includes its own 4-byte header
    return struct.pack("!HH", tlv_type, length) + value


def _cdp_frame(cdp_payload: bytes, src: bytes = b"\x11" * 6) -> bytes:
    dst = cdp._CDP_DEST_MAC
    length = b"\x00\x00"  # 802.3 length field, unused by the parser
    llc_snap = b"\xaa\xaa\x03" + cdp._CDP_SNAP_OUI + cdp._CDP_SNAP_PID
    return dst + src + length + llc_snap + cdp_payload


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


def test_handle_packet_ignores_wrong_dest_mac():
    cdp._neighbors.clear()
    packet = b"\x22" * 6 + b"\x11" * 6 + b"\x00\x00" + b"\xaa\xaa\x03" + cdp._CDP_SNAP_OUI + cdp._CDP_SNAP_PID

    cdp._handle_packet("eth0", packet)

    assert cdp._neighbors == {}


def test_handle_packet_ignores_non_snap_llc():
    cdp._neighbors.clear()
    packet = cdp._CDP_DEST_MAC + b"\x11" * 6 + b"\x00\x00" + b"\x00\x00\x00" + cdp._CDP_SNAP_OUI + cdp._CDP_SNAP_PID

    cdp._handle_packet("eth0", packet)

    assert cdp._neighbors == {}


def test_handle_packet_parses_and_caches_a_cdp_frame_keyed_by_source_mac():
    cdp._neighbors.clear()
    packet = _cdp_frame(_cdp_payload(_tlv(0x0001, b"switch01")))

    cdp._handle_packet("eth0", packet)

    assert cdp._neighbors["11:11:11:11:11:11"]["device_id"] == "switch01"
    assert cdp._neighbors["11:11:11:11:11:11"]["mac"] == "11:11:11:11:11:11"


def test_handle_packet_keeps_separate_neighbors_for_different_source_macs():
    cdp._neighbors.clear()
    cdp._handle_packet(
        "eth0", _cdp_frame(_cdp_payload(_tlv(0x0001, b"switch01")), src=b"\xaa" * 6)
    )
    cdp._handle_packet(
        "eth0", _cdp_frame(_cdp_payload(_tlv(0x0001, b"switch02")), src=b"\xbb" * 6)
    )

    assert cdp._neighbors["aa:aa:aa:aa:aa:aa"]["device_id"] == "switch01"
    assert cdp._neighbors["bb:bb:bb:bb:bb:bb"]["device_id"] == "switch02"
    assert len(cdp._neighbors) == 2


def test_get_neighbors_lists_all_fresh_neighbors(monkeypatch):
    cdp._neighbors.clear()
    cdp._started_interfaces.clear()
    monkeypatch.setattr(cdp, "start_listener", lambda interface="eth0": None)

    cdp._handle_packet(
        "eth0", _cdp_frame(_cdp_payload(_tlv(0x0001, b"switch01")), src=b"\xaa" * 6)
    )
    cdp._handle_packet(
        "eth0", _cdp_frame(_cdp_payload(_tlv(0x0001, b"switch02")), src=b"\xbb" * 6)
    )

    result = cdp.get_neighbors("eth0")

    assert result["present"] is True
    assert {n["device_id"] for n in result["neighbors"]} == {"switch01", "switch02"}


def test_get_neighbors_purges_stale_entries_from_the_cache_not_just_the_response(monkeypatch):
    cdp._neighbors.clear()
    cdp._started_interfaces.clear()

    times = iter([1000.0, 1100.0])  # one packet, then a read 100s later
    monkeypatch.setattr(cdp.time, "time", lambda: next(times))
    monkeypatch.setattr(cdp, "start_listener", lambda interface="eth0": None)

    cdp._handle_packet("eth0", _cdp_frame(_cdp_payload(_tlv(0x0001, b"gone"))))

    cdp.get_neighbors("eth0", stale_after=60.0)

    assert cdp._neighbors == {}


def test_default_stale_after_is_60_seconds():
    assert cdp._DEFAULT_STALE_AFTER == 60.0
