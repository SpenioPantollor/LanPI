"""Tests for backend/discovery/mndp.py: the pure TLV parser
(_parse_mndp_payload) and _handle_packet(), the dispatcher-facing entry
point that does the IPv4/UDP-port-5678 filter formerly done by a BPF
filter at the tcpdump level (see backend/capture/dispatcher.py)."""
import struct

from backend.discovery import mndp


def _tlv(tlv_type: int, value: bytes) -> bytes:
    return struct.pack("!HH", tlv_type, len(value)) + value


def _mac(s: str) -> bytes:
    return bytes(int(b, 16) for b in s.split(":"))


def _mndp_payload(tlvs: bytes) -> bytes:
    return b"\x00\x00\x00\x01" + tlvs  # 2-byte header + 2-byte sequence number


def _ip_udp_frame(
    sport: int, dport: int, payload: bytes, proto: int = 17, ethertype: int = 0x0800,
    src_mac: bytes = b"\x11" * 6,
) -> bytes:
    eth = b"\x00" * 6 + src_mac + struct.pack("!H", ethertype)
    ip_header = bytearray(20)
    ip_header[0] = 0x45  # version 4, IHL 5 (20 bytes)
    ip_header[9] = proto
    udp_header = struct.pack("!HHHH", sport, dport, 8 + len(payload), 0)
    return eth + bytes(ip_header) + udp_header + payload


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


def test_handle_packet_ignores_non_ipv4_ethertype():
    mndp._neighbors.clear()
    packet = _ip_udp_frame(12345, mndp._MNDP_PORT, _mndp_payload(b""), ethertype=0x86DD)

    mndp._handle_packet("eth0", packet)

    assert mndp._neighbors == {}


def test_handle_packet_ignores_non_udp_protocol():
    mndp._neighbors.clear()
    packet = _ip_udp_frame(12345, mndp._MNDP_PORT, _mndp_payload(b""), proto=6)  # TCP

    mndp._handle_packet("eth0", packet)

    assert mndp._neighbors == {}


def test_handle_packet_ignores_wrong_port():
    mndp._neighbors.clear()
    packet = _ip_udp_frame(12345, 9999, _mndp_payload(b""))

    mndp._handle_packet("eth0", packet)

    assert mndp._neighbors == {}


def test_handle_packet_parses_and_caches_an_mndp_frame_keyed_by_source_mac():
    mndp._neighbors.clear()
    packet = _ip_udp_frame(12345, mndp._MNDP_PORT, _mndp_payload(_tlv(0x0005, b"office-router")))

    mndp._handle_packet("eth0", packet)

    assert mndp._neighbors["11:11:11:11:11:11"]["identity"] == "office-router"
    assert mndp._neighbors["11:11:11:11:11:11"]["mac"] == "11:11:11:11:11:11"


def test_handle_packet_keeps_separate_neighbors_for_different_source_macs():
    mndp._neighbors.clear()
    mndp._handle_packet(
        "eth0",
        _ip_udp_frame(
            12345, mndp._MNDP_PORT, _mndp_payload(_tlv(0x0005, b"router-a")), src_mac=b"\xaa" * 6
        ),
    )
    mndp._handle_packet(
        "eth0",
        _ip_udp_frame(
            12345, mndp._MNDP_PORT, _mndp_payload(_tlv(0x0005, b"router-b")), src_mac=b"\xbb" * 6
        ),
    )

    assert mndp._neighbors["aa:aa:aa:aa:aa:aa"]["identity"] == "router-a"
    assert mndp._neighbors["bb:bb:bb:bb:bb:bb"]["identity"] == "router-b"
    assert len(mndp._neighbors) == 2


def test_get_neighbors_lists_all_fresh_neighbors(monkeypatch):
    mndp._neighbors.clear()
    mndp._started_interfaces.clear()
    monkeypatch.setattr(mndp, "start_listener", lambda interface="eth0": None)

    mndp._handle_packet(
        "eth0",
        _ip_udp_frame(
            12345, mndp._MNDP_PORT, _mndp_payload(_tlv(0x0005, b"router-a")), src_mac=b"\xaa" * 6
        ),
    )
    mndp._handle_packet(
        "eth0",
        _ip_udp_frame(
            12345, mndp._MNDP_PORT, _mndp_payload(_tlv(0x0005, b"router-b")), src_mac=b"\xbb" * 6
        ),
    )

    result = mndp.get_neighbors("eth0")

    assert result["present"] is True
    assert {n["identity"] for n in result["neighbors"]} == {"router-a", "router-b"}


def test_get_neighbors_purges_stale_entries_from_the_cache_not_just_the_response(monkeypatch):
    mndp._neighbors.clear()
    mndp._started_interfaces.clear()

    times = iter([1000.0, 1100.0])  # one packet, then a read 100s later
    monkeypatch.setattr(mndp.time, "time", lambda: next(times))
    monkeypatch.setattr(mndp, "start_listener", lambda interface="eth0": None)

    mndp._handle_packet(
        "eth0", _ip_udp_frame(12345, mndp._MNDP_PORT, _mndp_payload(_tlv(0x0005, b"gone")))
    )

    mndp.get_neighbors("eth0", stale_after=60.0)

    assert mndp._neighbors == {}


def test_default_stale_after_is_60_seconds():
    assert mndp._DEFAULT_STALE_AFTER == 60.0
