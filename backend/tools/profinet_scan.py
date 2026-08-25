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
