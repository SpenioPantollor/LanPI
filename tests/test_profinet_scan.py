"""Tests for backend/tools/profinet_scan.py: the pure frame builder/
parser (no real socket -- AF_PACKET doesn't exist off Linux, so
scan() itself isn't exercised here). Response fixture values match the
real-world worked example in nmap's multicast-profinet-discovery.nse
docstring (Siemens S7-300, vendorId 0x002A/deviceId 0x0105)."""
import socket
import struct

from backend.tools import profinet_scan


def _block(option: int, suboption: int, data: bytes) -> bytes:
    block = struct.pack(">BBH", option, suboption, len(data)) + data
    if len(data) & 1:
        block += b"\x00"  # odd-length block data is followed by one pad byte
    return block


def _response_frame(blocks: bytes, frame_id=0xFEFF, service_id=5, service_type=1,
                     src_mac=b"\x00\x0e\x8c\xc9\x41\x15") -> bytes:
    header = struct.pack(">HBBIHH", frame_id, service_id, service_type, 0x0FAB0001, 1, len(blocks))
    eth_header = profinet_scan._DCP_MULTICAST_MAC + src_mac + struct.pack(">H", profinet_scan._ETHERTYPE_PROFINET)
    return eth_header + header + blocks


def test_build_request_has_correct_header_and_min_length():
    src_mac = b"\xaa\xbb\xcc\xdd\xee\xff"
    frame = profinet_scan._build_request(src_mac)

    assert len(frame) >= 60
    assert frame[0:6] == profinet_scan._DCP_MULTICAST_MAC
    assert frame[6:12] == src_mac
    assert struct.unpack(">H", frame[12:14])[0] == 0x8892

    frame_id, service_id, service_type, _xid, _delay, data_len = struct.unpack(">HBBIHH", frame[14:26])
    assert frame_id == 0xFEFE
    assert service_id == 5
    assert service_type == 0
    assert data_len == 4
    assert frame[26:30] == b"\xff\xff\x00\x00"


def test_parse_response_ignores_non_profinet_ethertype():
    frame = b"\x00" * 12 + struct.pack(">H", 0x0800) + b"\x00" * 20
    assert profinet_scan._parse_response(frame) is None


def test_parse_response_ignores_wrong_frame_id_or_service():
    frame = _response_frame(b"", frame_id=0x1234)
    assert profinet_scan._parse_response(frame) is None

    frame = _response_frame(b"", service_type=0)  # a request, not a response
    assert profinet_scan._parse_response(frame) is None


def test_parse_response_extracts_mac_from_ethernet_source():
    frame = _response_frame(b"", src_mac=b"\x00\x0e\x8c\xc9\x41\x15")
    device = profinet_scan._parse_response(frame)
    assert device["mac"] == "00:0e:8c:c9:41:15"


def test_parse_response_extracts_ip_parameter_block():
    ip_block_data = struct.pack(
        ">HIII", 1, int.from_bytes(socket.inet_aton("10.253.81.37"), "big"),
        int.from_bytes(socket.inet_aton("255.255.255.0"), "big"),
        int.from_bytes(socket.inet_aton("10.253.81.1"), "big"),
    )
    blocks = _block(profinet_scan._OPT_IP, profinet_scan._SUB_IP_PARAMETER, ip_block_data)
    device = profinet_scan._parse_response(_response_frame(blocks))

    assert device["ip"] == "10.253.81.37"
    assert device["subnet_mask"] == "255.255.255.0"
    assert device["gateway"] == "10.253.81.1"
    assert device["ip_info"] == "IP set"


def test_parse_response_extracts_device_properties_and_handles_odd_length_padding():
    # "pn-io" (5 bytes) -- an odd-length string forces the block's pad byte,
    # exercising the odd-length-block padding rule for the block that follows.
    name_block = _block(profinet_scan._OPT_DEVICE, profinet_scan._SUB_DEV_NAME_OF_STATION,
                         b"\x00\x00" + b"pn-io")
    vendor_block = _block(profinet_scan._OPT_DEVICE, profinet_scan._SUB_DEV_VENDOR_VALUE,
                           b"\x00\x00" + b"S7-300")
    id_block = _block(profinet_scan._OPT_DEVICE, profinet_scan._SUB_DEV_ID,
                       b"\x00\x00" + struct.pack(">HH", 0x002A, 0x0105))
    role_block = _block(profinet_scan._OPT_DEVICE, profinet_scan._SUB_DEV_ROLE,
                         b"\x00\x00" + b"\x00\x00")  # role 0x00 = None

    blocks = name_block + vendor_block + id_block + role_block
    device = profinet_scan._parse_response(_response_frame(blocks))

    assert device["name_of_station"] == "pn-io"
    assert device["name_of_station_decoded"] is None  # "-io" tail isn't valid hex -- not this pattern
    assert device["vendor_value"] == "S7-300"
    assert device["vendor_id"] == "0x002a"
    assert device["device_id"] == "0x0105"
    assert device["device_role"] == "None"


def test_crc16_arc_matches_standard_check_value():
    # The published CRC-16/ARC check value for the ASCII string "123456789"
    # is 0xbb3d -- confirms this is the right CRC variant before trusting
    # it to validate/generate real device names.
    assert profinet_scan._crc16_arc(b"123456789") == 0xBB3D


def test_decode_siemens_station_name_matches_confirmed_real_world_pairs():
    # Both pairs confirmed 2026-08-25 against real devices on the same
    # segment -- CRC-verified, not a guess (see profinet_scan.py's
    # docstring above _decode_siemens_station_name).
    assert profinet_scan._decode_siemens_station_name("prodxbtalpxbplcf320") == "prod_talp_plc"
    assert profinet_scan._decode_siemens_station_name("k1cjf11xbcpu1a19e") == "k1cjf11_cpu1"


def test_decode_siemens_station_name_decodes_plus_and_equals_escapes():
    body = "axnbxvc"  # "a" + "xn"(->+) + "b" + "xv"(->=) + "c" = "a+b=c"
    suffix = f"{profinet_scan._crc16_arc(body.encode('ascii')):04x}"
    assert profinet_scan._decode_siemens_station_name(body + suffix) == "a+b=c"


def test_decode_siemens_station_name_rejects_crc_mismatch():
    # Ends in 4 hex chars, but they aren't a valid CRC of the rest --
    # a name that merely looks like this pattern by coincidence, not a
    # real TIA-Portal-converted one. Must not decode.
    assert profinet_scan._decode_siemens_station_name("prodxbtalpxbplcffff") is None


def test_decode_siemens_station_name_returns_none_for_non_matching_names():
    assert profinet_scan._decode_siemens_station_name("pn-io") is None  # too short / no valid CRC tail
    assert profinet_scan._decode_siemens_station_name(None) is None


def test_parse_response_populates_decoded_name_for_a_real_confirmed_pair():
    name_block = _block(profinet_scan._OPT_DEVICE, profinet_scan._SUB_DEV_NAME_OF_STATION,
                         b"\x00\x00" + b"k1cjf11xbcpu1a19e")
    device = profinet_scan._parse_response(_response_frame(name_block))

    assert device["name_of_station"] == "k1cjf11xbcpu1a19e"
    assert device["name_of_station_decoded"] == "k1cjf11_cpu1"


def test_interface_mac_reads_sysfs_address(tmp_path, monkeypatch):
    (tmp_path / "eth0").write_text("aa:bb:cc:dd:ee:ff\n")
    monkeypatch.setattr(profinet_scan, "_MAC_PATH_TMPL", str(tmp_path / "{}"))

    mac = profinet_scan._interface_mac("eth0")
    assert mac == b"\xaa\xbb\xcc\xdd\xee\xff"


def test_interface_mac_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(profinet_scan, "_MAC_PATH_TMPL", str(tmp_path) + "/does-not-exist-{}")
    assert profinet_scan._interface_mac("eth0") is None
