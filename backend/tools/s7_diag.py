"""Siemens S7 CPU identification, read-only, on the TEST PORT (eth0).

Establishes a COTP/S7comm session (the same handshake any S7 PLC
programming/HMI tool uses) and reads the CPU's own System Status List
(SZL) -- module type, order number, firmware version, serial number,
plant ID -- via the standard "Read SZL" service. This is pure
identification, the S7 equivalent of Modbus's Device Identification
(FC43) in modbus.py: no DB/memory read or write, no PLC control
(start/stop), nothing that could affect a running process. S7 "Read/
Write Var" (actual process data access) is a distinct, much larger
scope and isn't implemented here.

Hand-rolled, no python-snap7/pysnmp-style dependency -- consistent
with this project's other protocol clients (LLDP/CDP/MNDP, Modbus,
PROFINET DCP). The exact COTP Connection Request / Setup Communication
/ Read SZL byte sequences and the SZL response field offsets are taken
verbatim from nmap's `s7-info.nse` (a real-world tested reference
implementation used broadly for S7 identification), not derived from
the S7comm spec by hand -- S7comm itself is not officially published
by Siemens; nmap's script (and the open-source Snap7/plcscan/Wireshark
dissector projects it's based on) is the closest thing to a de facto
reference. Two COTP TSAP pairs are tried (rack 0/slot 2, then a
fallback for other CPU families) since different S7 generations
default to different rack/slot addressing.

Sourced from eth0's current address (socket bind) AND the eth0 device
itself (SO_BINDTODEVICE), same reasoning as tcp_test.py/modbus.py: an
address-only bind isn't enough when eth0 and wlan0 both have a route
to the same subnet.
"""

from __future__ import annotations

import socket
import struct
import time

from backend.network import eth0_mode

# COTP Connection Request (TPKT + COTP CR), proposing calling TSAP
# 0x0100 (standard "PG" local TSAP) and one of two called TSAPs:
# 0x0102 (rack 0 / slot 2 -- typical S7-300/400 CPU slot) or, if that
# isn't confirmed, 0x0200 (a different TSAP type/rack-slot pairing
# some other CPU families use as their default). TPDU size proposed:
# 2^10 = 1024 bytes.
_COTP_CR_PRIMARY = bytes.fromhex("0300001611e00000001400c1020100c2020102c0010a")
_COTP_CR_FALLBACK = bytes.fromhex("0300001611e00000000500c1020100c2020200c0010a")

# S7comm "Setup Communication" job request (function 0xF0): proposes
# max outstanding calling/called PDUs of 1 and a PDU length of 0x01e0
# (480 bytes).
_SETUP_COMMUNICATION = bytes.fromhex("0300001902f08032010000000000080000f0000001000101e0")

# S7comm "Read SZL" Userdata requests (function group 0x4/CPU
# functions, subfunction 0x01), differing only in the SZL-ID at the
# end: 0x0011 (module identification -- order number, version) and
# 0x001c (component identification -- system name, module type,
# serial number, plant ID, copyright).
_READ_SZL_MODULE_IDENTIFICATION = bytes.fromhex(
    "0300002102f080320700000000000800080001120411440100ff09000400110001"
)
_READ_SZL_COMPONENT_IDENTIFICATION = bytes.fromhex(
    "0300002102f080320700000000000800080001120411440100ff090004001c0001"
)

_COTP_CONNECT_CONFIRM = 0xD0
_S7_PROTOCOL_ID = 0x32


def _eth0_source_ip() -> str | None:
    mode = eth0_mode.get_mode()
    address = mode.get("address")
    return address.split("/")[0] if address else None


def _bind_to_eth0_device(sock: socket.socket) -> None:
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"eth0")
    except (OSError, AttributeError):
        # OSError: no CAP_NET_RAW. AttributeError: SO_BINDTODEVICE
        # doesn't exist on this platform (e.g. macOS, used for local
        # dev testing -- it's Linux-only). Either way, falls back to
        # address-only binding.
        pass


def _recv_exact(sock: socket.socket, count: int) -> bytes | None:
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def _recv_tpkt_frame(sock: socket.socket) -> bytes | None:
    """Reads one full TPKT frame (header + everything its own length
    field says follows) rather than guessing a fixed byte count."""
    header = _recv_exact(sock, 4)
    if header is None:
        return None
    total_len = struct.unpack("!H", header[2:4])[0]
    if total_len < 4:
        return None
    rest = _recv_exact(sock, total_len - 4)
    if rest is None:
        return None
    return header + rest


def _cstring(data: bytes, offset: int) -> str | None:
    if offset < 0 or offset >= len(data):
        return None
    end = data.find(b"\x00", offset)
    if end == -1:
        return None
    text = data[offset:end].decode("ascii", errors="replace").strip()
    return text or None


def _parse_module_identification(frame: bytes) -> dict:
    """SZL 0x0011 response -- field offsets from nmap's s7-info.nse
    parse_response() (1-indexed there; converted to 0-indexed here)."""
    result: dict = {"module": None, "basic_hardware": None, "version": None}
    if len(frame) < 8 or frame[7] != _S7_PROTOCOL_ID or len(frame) < 125:
        return result
    result["module"] = _cstring(frame, 43)
    result["basic_hardware"] = _cstring(frame, 71)
    v1, v2, v3 = frame[122], frame[123], frame[124]
    result["version"] = f"{v1}.{v2}.{v3}"
    return result


def _parse_component_identification(frame: bytes) -> dict:
    """SZL 0x001c response -- field offsets from nmap's s7-info.nse
    second_parse_response(). That function applies a +4 byte offset to
    every field when the SZL-ID echoed back in the response isn't
    0x1c (some CPU families' 0x001c response is laid out 4 bytes
    later than others) -- an empirically-discovered quirk, not
    something derivable from the protocol alone, reproduced as-is."""
    result: dict = {
        "system_name": None, "module_type": None, "serial_number": None,
        "plant_identification": None, "copyright": None,
    }
    if len(frame) < 31 or frame[7] != _S7_PROTOCOL_ID:
        return result
    offset = 0 if frame[30] == 0x1C else 4
    result["system_name"] = _cstring(frame, 39 + offset)
    result["module_type"] = _cstring(frame, 73 + offset)
    result["plant_identification"] = _cstring(frame, 107 + offset)
    result["copyright"] = _cstring(frame, 141 + offset)
    result["serial_number"] = _cstring(frame, 175 + offset)
    return result


def _open_and_negotiate_cotp(
    host: str, port: int, source_ip: str, timeout: float
) -> socket.socket | None:
    for connection_request in (_COTP_CR_PRIMARY, _COTP_CR_FALLBACK):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        _bind_to_eth0_device(sock)
        sock.bind((source_ip, 0))
        sock.connect((host, port))
        sock.sendall(connection_request)
        response = _recv_tpkt_frame(sock)
        if response is not None and len(response) >= 6 and response[5] == _COTP_CONNECT_CONFIRM:
            return sock
        sock.close()
    return None


def identify(host: str, port: int = 102, timeout: float = 5.0) -> dict:
    host = (host or "").strip()
    if not host:
        return {"ok": False, "connected": False, "message": "host is required"}
    if not (1 <= port <= 65535):
        return {"ok": False, "connected": False, "message": "port must be 1-65535"}

    source_ip = _eth0_source_ip()
    if not source_ip:
        return {
            "ok": False,
            "connected": False,
            "message": "eth0 has no IP address -- switch to DHCP or Static mode first "
                       "(Passive mode has no source address to connect from)",
        }

    started = time.monotonic()

    def elapsed_ms() -> float:
        return round((time.monotonic() - started) * 1000, 1)

    sock = None
    try:
        sock = _open_and_negotiate_cotp(host, port, source_ip, timeout)
        if sock is None:
            return {
                "ok": False,
                "connected": False,
                "message": "COTP connection not confirmed -- not an S7-compatible device, "
                           "or a different rack/slot than the two defaults tried",
                "response_time_ms": elapsed_ms(),
            }

        sock.sendall(_SETUP_COMMUNICATION)
        setup_response = _recv_tpkt_frame(sock)
        if setup_response is None or len(setup_response) < 8 or setup_response[7] != _S7_PROTOCOL_ID:
            return {
                "ok": False,
                "connected": True,
                "message": "no valid S7comm response to Setup Communication",
                "response_time_ms": elapsed_ms(),
            }

        sock.sendall(_READ_SZL_MODULE_IDENTIFICATION)
        module_frame = _recv_tpkt_frame(sock)
        module_info = _parse_module_identification(module_frame) if module_frame else {}

        sock.sendall(_READ_SZL_COMPONENT_IDENTIFICATION)
        component_frame = _recv_tpkt_frame(sock)
        component_info = _parse_component_identification(component_frame) if component_frame else {}
    except socket.timeout:
        return {"ok": False, "connected": False, "message": "timeout -- no response from device",
                "response_time_ms": elapsed_ms()}
    except ConnectionRefusedError:
        return {"ok": False, "connected": False, "message": f"connection refused -- port {port} not open",
                "response_time_ms": elapsed_ms()}
    except OSError as exc:
        return {"ok": False, "connected": False, "message": str(exc), "response_time_ms": elapsed_ms()}
    finally:
        if sock is not None:
            sock.close()

    result = {"ok": True, "connected": True, "message": None, "response_time_ms": elapsed_ms()}
    result.update(module_info)
    result.update(component_info)
    return result
