"""Active PROFINET DCP Identify-All scan on the TEST PORT (eth0).

Sends a single multicast DCP Identify request (destination MAC
01:0e:cf:00:00:00, EtherType 0x8892) and collects Identify responses
for a bounded window. This is the DCP "Identify" service only --
read-only, no device state is changed. ARCHITECTURE.MD Rule 5 flags
DCP as allowed by default only for Identify; DCP Set (renaming a
device, reassigning its IP) is a distinct, more dangerous service and
is deliberately not implemented here.

PROFINET DCP is a non-IP Layer-2 protocol -- none of the existing
IP-based scanners (arp-scan, nmap) can speak it, so this builds and
parses the Ethernet frame directly over a raw AF_PACKET socket. Uses
the same CAP_NET_RAW ambient capability already granted to the service
for tcp_test.py/modbus.py's SO_BINDTODEVICE use (see
system/lanpi.service) -- no sudo needed, and (unlike ip_scanner.py's
nmap-via-sudo route) no external binary dependency either.

Frame layout (request build + response parse, including the
odd-length-block padding rule) verified against nmap's
multicast-profinet-discovery.nse -- a real-world tested reference
implementation -- and cross-checked against Wireshark's
packet-pn-dcp.c for the padding rule specifically. Device MAC comes
from the Ethernet source address, not a DCP block (real devices don't
send one for Identify).
"""

from __future__ import annotations

import os
import socket
import struct
import time

_ETHERTYPE_PROFINET = 0x8892
_DCP_MULTICAST_MAC = bytes.fromhex("010ecf000000")
_MAC_PATH_TMPL = "/sys/class/net/{}/address"
_MIN_FRAME_LEN = 60  # Ethernet minimum frame size, excluding the FCS

_FRAME_ID_IDENTIFY_REQUEST = 0xFEFE
_FRAME_ID_IDENTIFY_RESPONSE = 0xFEFF
_SERVICE_ID_IDENTIFY = 0x05
_SERVICE_TYPE_REQUEST = 0x00
_SERVICE_TYPE_RESPONSE_SUCCESS = 0x01
_RESPONSE_DELAY_FACTOR = 100  # units of 10ms -- devices randomize their reply within this window

_OPT_IP = 0x01
_SUB_IP_PARAMETER = 0x02
_OPT_DEVICE = 0x02
_SUB_DEV_VENDOR_VALUE = 0x01
_SUB_DEV_NAME_OF_STATION = 0x02
_SUB_DEV_ID = 0x03
_SUB_DEV_ROLE = 0x04

_DEVICE_ROLE_FLAGS = {
    0x01: "IO-Device",
    0x02: "IO-Controller",
    0x04: "IO-Multidevice",
    0x08: "PN-Supervisor",
}
_IP_INFO_TEXT = {0: "no IP set", 1: "IP set", 2: "IP set via DHCP"}

# PROFINET station names are restricted by spec (IEC 61158-6-10, DCP
# NameOfStation, confirmed 2026-08-25): only lowercase a-z, 0-9, "-",
# "." are legal (plus length limits -- 240 chars overall, 63 per
# "."-separated component -- and a name can't start/end with "-",
# start with "port-xyz", or look like an IPv4 address). "_"/"+"/"=" are
# not in that set, so a human-entered engineering name containing one
# (as TIA Portal device names commonly do) can't go on the wire as-is
# -- TIA Portal derives a compliant "Converted Name" from it instead
# (an official TIA Portal term/UI field, not our own name for it).
#
# The exact derivation (source: Siemens SiePortal forum, "Procedure to
# convert PROFINET device names", post 301814 -- the live page needs
# JS/login and couldn't be fetched directly, content relayed
# 2026-08-25): lowercase the name, replace each disallowed character
# with a 2-character "x"+token (table below), then append a CRC-16/ARC
# (poly 0x8005, init 0x0000, reflected in/out, xorout 0x0000 -- the
# classic "CRC-16"/ARC/IBM variant) of the escaped string, as 4
# lowercase hex digits. The CRC step is **provable, not a heuristic**:
# our CRC-16/ARC implementation reproduces the standard check value
# (0xbb3d for "123456789") and exactly reproduces both real suffixes
# seen live on this segment --
#   "prodxbtalpxbplc"  -> CRC f320 -> "prodxbtalpxbplcf320" (raw)
#     decodes to "prod_talp_plc"
#   "k1cjf11xbcpu1"    -> CRC a19e -> "k1cjf11xbcpu1a19e"   (raw)
#     decodes to "k1cjf11_cpu1"
# both confirmed against the maintainer's real (TIA-Portal-configured)
# device names. Decoding recomputes the CRC over everything but the
# last 4 hex characters and only unescapes if it matches -- this is
# what makes it safe to apply to *any* device (not gated to a specific
# vendor_id): a name that was never TIA-Portal-converted will only
# "accidentally" pass by pure chance (1 in 65536), vs. a substring-only
# check like "contains xb" which has no such guarantee at all. Note the
# CRC only proves "this raw name + suffix is a valid TIA-Portal-style
# converted pair" -- it does NOT independently confirm every entry in
# the escape table below is the character TIA Portal actually meant
# (that would need re-encoding and comparing), so a wrong table entry
# could in principle still produce a plausible-looking wrong decode.
#
# Escape table: "_"/"+"/"=" come from the SiePortal post above; the
# rest were reverse-engineered directly against real TIA Portal by the
# maintainer (each character tested individually and observed in the
# resulting Converted Name). No formula found relating the character
# to its token (checked ASCII value mod 26/mod 36 and simple offsets
# against all 10 known pairs -- none fit consistently, and "[" mapping
# to a *digit* ("x2") rather than a letter rules out any single
# alphabetic-only scheme) -- this looks like a fixed lookup table
# Siemens hard-coded, not something computable, so further characters
# can only be added empirically as they're found.
_SIEMENS_ESCAPE_TO_CHAR = {
    "xa": " ",
    "xb": "_",
    "xd": ".",
    "xh": "~",
    "xm": "*",
    "xn": "+",
    "xr": "/",
    "xu": "\\",
    "xv": "=",
    "x2": "[",
}
_SIEMENS_SUFFIX_LEN = 4


def _crc16_arc(data: bytes) -> int:
    crc = 0x0000
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc & 0xFFFF


def _decode_siemens_station_name(raw_name: str | None) -> str | None:
    if not raw_name or len(raw_name) <= _SIEMENS_SUFFIX_LEN:
        return None
    body, suffix = raw_name[:-_SIEMENS_SUFFIX_LEN], raw_name[-_SIEMENS_SUFFIX_LEN:]
    try:
        int(suffix, 16)
        body_bytes = body.encode("ascii")
    except (ValueError, UnicodeEncodeError):
        return None
    if f"{_crc16_arc(body_bytes):04x}" != suffix:
        return None

    decoded = body
    for token, char in _SIEMENS_ESCAPE_TO_CHAR.items():
        decoded = decoded.replace(token, char)
    return decoded


def _interface_mac(interface: str) -> bytes | None:
    try:
        with open(_MAC_PATH_TMPL.format(interface)) as f:
            text = f.read().strip()
        mac = bytes.fromhex(text.replace(":", ""))
        return mac if len(mac) == 6 else None
    except (OSError, ValueError):
        return None


def _build_request(src_mac: bytes) -> bytes:
    header = struct.pack(
        ">HBBIHH",
        _FRAME_ID_IDENTIFY_REQUEST,
        _SERVICE_ID_IDENTIFY,
        _SERVICE_TYPE_REQUEST,
        int.from_bytes(os.urandom(4), "big"),  # Xid -- not validated on receipt, matches the reference implementation
        _RESPONSE_DELAY_FACTOR,
        0x0004,  # DCPDataLength: the Option+Suboption+BlockLength that follow
    )
    all_selector = struct.pack(">BBH", 0xFF, 0xFF, 0x0000)  # Option/Suboption "all", no block data

    frame = _DCP_MULTICAST_MAC + src_mac + struct.pack(">H", _ETHERTYPE_PROFINET) + header + all_selector
    if len(frame) < _MIN_FRAME_LEN:
        frame += b"\x00" * (_MIN_FRAME_LEN - len(frame))
    return frame


def _parse_device_block(suboption: int, data: bytes, device: dict) -> None:
    if suboption == _SUB_DEV_VENDOR_VALUE:
        device["vendor_value"] = data[2:].decode("utf-8", errors="replace")
    elif suboption == _SUB_DEV_NAME_OF_STATION:
        device["name_of_station"] = data[2:].decode("utf-8", errors="replace")
    elif suboption == _SUB_DEV_ID and len(data) >= 6:
        vendor_id, device_id = struct.unpack(">HH", data[2:6])
        device["vendor_id"] = f"0x{vendor_id:04x}"
        device["device_id"] = f"0x{device_id:04x}"
    elif suboption == _SUB_DEV_ROLE and len(data) >= 3:
        role = data[2]
        names = [name for flag, name in _DEVICE_ROLE_FLAGS.items() if role & flag]
        device["device_role"] = ", ".join(names) if names else ("None" if role == 0 else None)


def _parse_ip_block(suboption: int, data: bytes, device: dict) -> None:
    if suboption == _SUB_IP_PARAMETER and len(data) >= 14:
        block_info, ip_raw, mask_raw, gw_raw = struct.unpack(">HIII", data[:14])
        device["ip"] = socket.inet_ntoa(struct.pack(">I", ip_raw)) if ip_raw else None
        device["subnet_mask"] = socket.inet_ntoa(struct.pack(">I", mask_raw)) if mask_raw else None
        device["gateway"] = socket.inet_ntoa(struct.pack(">I", gw_raw)) if gw_raw else None
        device["ip_info"] = _IP_INFO_TEXT.get(block_info & 0x0F)


def _parse_response(frame: bytes) -> dict | None:
    if len(frame) < 14 + 12:
        return None
    ethertype = struct.unpack(">H", frame[12:14])[0]
    if ethertype != _ETHERTYPE_PROFINET:
        return None

    frame_id, service_id, service_type, _xid, _delay, data_len = struct.unpack(">HBBIHH", frame[14:26])
    if frame_id != _FRAME_ID_IDENTIFY_RESPONSE or service_id != _SERVICE_ID_IDENTIFY:
        return None
    if service_type != _SERVICE_TYPE_RESPONSE_SUCCESS:
        return None

    device: dict = {
        "mac": ":".join(f"{b:02x}" for b in frame[6:12]),
        "ip": None,
        "subnet_mask": None,
        "gateway": None,
        "ip_info": None,
        "name_of_station": None,
        "name_of_station_decoded": None,
        "vendor_value": None,
        "vendor_id": None,
        "device_id": None,
        "device_role": None,
    }

    offset = 26
    end = min(26 + data_len, len(frame))
    while offset + 4 <= end:
        option, suboption, block_len = struct.unpack(">BBH", frame[offset:offset + 4])
        block_start = offset + 4
        block_end = block_start + block_len
        if block_end > len(frame):
            break
        block_data = frame[block_start:block_end]

        if option == _OPT_DEVICE:
            _parse_device_block(suboption, block_data, device)
        elif option == _OPT_IP:
            _parse_ip_block(suboption, block_data, device)

        offset = block_end + (block_len & 1)  # odd-length block data is followed by one pad byte

    device["name_of_station_decoded"] = _decode_siemens_station_name(device["name_of_station"])
    return device


def scan(interface: str = "eth0", timeout: float = 3.0) -> dict:
    src_mac = _interface_mac(interface)
    if not src_mac:
        return {"ok": False, "message": f"no MAC address for {interface}", "devices": []}

    try:
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(_ETHERTYPE_PROFINET))
        sock.bind((interface, _ETHERTYPE_PROFINET))
    except PermissionError:
        return {"ok": False, "message": "not permitted to open a raw socket (need CAP_NET_RAW)", "devices": []}
    except AttributeError:
        return {"ok": False, "message": "raw AF_PACKET sockets aren't available on this platform (Linux only)", "devices": []}
    except OSError as exc:
        return {"ok": False, "message": str(exc), "devices": []}

    devices: dict[str, dict] = {}
    try:
        sock.send(_build_request(src_mac))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(min(0.25, remaining))
            try:
                frame, _addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            device = _parse_response(frame)
            if device:
                devices[device["mac"]] = device
    finally:
        sock.close()

    return {"ok": True, "message": None, "devices": list(devices.values())}
