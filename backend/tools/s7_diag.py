"""Siemens S7 CPU identification and tag reading, read-only, on the
TEST PORT (eth0).

Establishes a COTP/S7comm session (the same handshake any S7 PLC
programming/HMI tool uses). Two things are exposed:

- identify(): reads the CPU's own System Status List (SZL) -- module
  type, order number, firmware version, serial number, plant ID --
  via the standard "Read SZL" service. The S7 equivalent of Modbus's
  Device Identification (FC43) in modbus.py.
- read_tag(): reads a single process-data value (a DB/M/I/Q address)
  via the standard "Read Var" service, given an area, offset and data
  type -- the S7 equivalent of a Modbus register read.

Both are strictly read-only: no memory write, no PLC control (start/
stop), nothing that could affect a running process. S7 "Write Var" is
deliberately not implemented -- consistent with LanPi's non-disruptive
diagnostics-only philosophy.

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

# S7comm "Read Var" (function 0x04) addresses process data with the
# standard "S7ANY" addressing scheme: an area code, an optional DB
# number, and a bit-granular address (byte_offset*8 + bit_offset).
# Area codes and the byte/word/dword-collapses-to-BYTE-transport-size
# convention below match every open-source S7 client (Snap7, libnodave,
# python-snap7) -- this is a stable, decades-old wire format, not
# something Siemens has ever officially published on its own.
_S7_AREA_CODES = {"I": 0x81, "Q": 0x82, "M": 0x83, "DB": 0x84}

# Byte size of one element for each supported data type. WORD/DWORD
# are unsigned; INT/DINT are signed; REAL is IEEE 754 single-precision
# -- all big-endian on the wire, as with every other S7 numeric field.
_S7_TYPE_SIZES = {"BIT": 1, "BYTE": 1, "WORD": 2, "INT": 2, "DWORD": 4, "DINT": 4, "REAL": 4}

_S7ANY_SYNTAX_ID = 0x10
_S7_TRANSPORT_SIZE_BIT = 0x01
_S7_TRANSPORT_SIZE_BYTE = 0x02
_S7_READ_VAR_FUNCTION = 0x04


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


def _build_read_var_request(area_code: int, db_number: int, byte_offset: int, bit_offset: int, data_type: str) -> bytes:
    """Builds a one-item S7comm Read Var job request. Verified against
    a real-world worked example (reading DB1, byte offset 4, as BYTE):
    the item's transport-size/count/area/address fields below produce
    exactly the same bytes as that example."""
    is_bit = data_type == "BIT"
    transport_size = _S7_TRANSPORT_SIZE_BIT if is_bit else _S7_TRANSPORT_SIZE_BYTE
    count = 1 if is_bit else _S7_TYPE_SIZES[data_type]
    bit_address = byte_offset * 8 + (bit_offset if is_bit else 0)
    item = struct.pack(
        "!BBBBHHBBBB",
        0x12, 0x0A, _S7ANY_SYNTAX_ID, transport_size, count, db_number,
        area_code, (bit_address >> 16) & 0xFF, (bit_address >> 8) & 0xFF, bit_address & 0xFF,
    )
    parameter = struct.pack("!BB", _S7_READ_VAR_FUNCTION, 1) + item
    header = struct.pack("!BBHHHH", _S7_PROTOCOL_ID, 0x01, 0x0000, 0x0001, len(parameter), 0x0000)
    cotp = bytes([0x02, 0xF0, 0x80])
    s7_pdu = header + parameter
    tpkt = struct.pack("!BBH", 0x03, 0x00, 4 + len(cotp) + len(s7_pdu))
    return tpkt + cotp + s7_pdu


def _parse_read_var_response(frame: bytes, expected_bytes: int) -> tuple[bytes | None, str | None]:
    """Returns (raw_data, error_message) -- exactly one of the two is
    None. Unlike the SZL/UserData responses above (rosctr 0x07, whose
    header stays the plain 10 bytes for both request and response), a
    Read Var response is Ack_Data (rosctr 0x03) -- and Ack/Ack_Data
    PDUs carry two extra header bytes, error class + error code, right
    after the normal 10-byte header and before the parameter section
    (confirmed live 2026-08-25: without this +2, the byte read as the
    item's own return code was actually the parameter section's own
    function-code byte, 0x04 -- a plausible-looking but wrong "return
    code" that happened to have no entry in _SZL_RETURN_CODE_MESSAGES
    either, which is what surfaced this). So the data section here
    starts at 19 + param_len, not the SZL functions' 17 + param_len.
    The item's own return code reuses _SZL_RETURN_CODE_MESSAGES since
    it's the same S7comm-wide DataItem return-code table (0xFF
    success, 0x0A object does not exist, ...)."""
    if len(frame) < 19 or frame[7] != _S7_PROTOCOL_ID:
        return None, "invalid or no response"
    rosctr = frame[8]
    if rosctr not in (0x02, 0x03):
        return None, f"unexpected response type (rosctr 0x{rosctr:02x})"
    error_class, error_code = frame[17], frame[18]
    if error_class != 0 or error_code != 0:
        return None, f"S7 PDU error (class 0x{error_class:02x}, code 0x{error_code:02x})"
    param_len = struct.unpack("!H", frame[13:15])[0]
    item_offset = 19 + param_len
    if item_offset + 4 > len(frame):
        return None, "short response"
    return_code = frame[item_offset]
    if return_code != _SZL_RETURN_CODE_SUCCESS:
        return None, _SZL_RETURN_CODE_MESSAGES.get(
            return_code, f"read failed (return code 0x{return_code:02x})"
        )
    data_start = item_offset + 4
    data = frame[data_start:data_start + expected_bytes]
    if len(data) < expected_bytes:
        return None, "truncated data in response"
    return bytes(data), None


def _decode_tag_value(data_type: str, raw: bytes):
    if data_type == "BIT":
        return bool(raw[0] & 0x01)
    if data_type == "BYTE":
        return raw[0]
    if data_type == "WORD":
        return struct.unpack("!H", raw)[0]
    if data_type == "INT":
        return struct.unpack("!h", raw)[0]
    if data_type == "DWORD":
        return struct.unpack("!I", raw)[0]
    if data_type == "DINT":
        return struct.unpack("!i", raw)[0]
    return struct.unpack("!f", raw)[0]  # REAL


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


# S7comm's standard DataItem return code, in the Read SZL response's
# data section (first byte, right after the S7 header + parameter):
# 0xFF is success; anything else means the request itself was
# rejected (no SZL data follows in that case). Confirmed live
# 2026-08-25 against a real S7-1200 (CPU 1214C): its module
# identification (SZL 0x0011) succeeded, but the component
# identification request (SZL 0x001c) came back with return code
# 0x0a ("Object does not exist") and only 4 bytes of data -- that
# specific CPU/firmware simply doesn't implement this SZL, not a
# parsing bug (the field-extraction functions above already degrade
# safely to all-None on a too-short frame; this just makes *why*
# explicit instead of silently blank fields).
_SZL_RETURN_CODE_SUCCESS = 0xFF
_SZL_RETURN_CODE_MESSAGES = {
    0x01: "hardware fault",
    0x03: "accessing object not allowed",
    0x05: "invalid address",
    0x06: "data type not supported",
    0x07: "data type inconsistent",
    0x0A: "object does not exist -- SZL not implemented by this CPU",
}


def _szl_return_code(frame: bytes) -> int | None:
    if len(frame) < 17:
        return None
    param_len = struct.unpack("!H", frame[13:15])[0]
    data_offset = 17 + param_len
    if data_offset >= len(frame):
        return None
    return frame[data_offset]


def _szl_failure_message(frame: bytes) -> str | None:
    code = _szl_return_code(frame)
    if code is None or code == _SZL_RETURN_CODE_SUCCESS:
        return None
    return _SZL_RETURN_CODE_MESSAGES.get(code, f"SZL request failed (return code 0x{code:02x})")


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
    """Tries each TSAP pairing on its own fresh connection, same as
    nmap's s7-info.nse. Some real CPUs (S7-1200/1500 in particular)
    reject an unrecognized TSAP with a hard TCP reset rather than a
    clean COTP-level non-confirm (confirmed live 2026-08-25 against a
    real S7-1200: "Connection reset by peer" mid-negotiation on the
    first attempt) -- so a connection-level OSError on one attempt
    must still let the next TSAP get tried, not abort the whole
    function. Only re-raises (for identify()'s own except clauses to
    turn into a message) if every attempt failed at the connection
    level; a clean-but-non-confirming response just moves on to the
    next TSAP with no error recorded."""
    last_exc: OSError | None = None
    for connection_request in (_COTP_CR_PRIMARY, _COTP_CR_FALLBACK):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            _bind_to_eth0_device(sock)
            sock.bind((source_ip, 0))
            sock.connect((host, port))
            sock.sendall(connection_request)
            response = _recv_tpkt_frame(sock)
        except OSError as exc:
            last_exc = exc
            sock.close()
            continue
        if response is not None and len(response) >= 6 and response[5] == _COTP_CONNECT_CONFIRM:
            return sock
        sock.close()
    if last_exc is not None:
        raise last_exc
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
        module_info["module_identification_error"] = (
            _szl_failure_message(module_frame) if module_frame else "no response"
        )

        sock.sendall(_READ_SZL_COMPONENT_IDENTIFICATION)
        component_frame = _recv_tpkt_frame(sock)
        component_info = _parse_component_identification(component_frame) if component_frame else {}
        component_info["component_identification_error"] = (
            _szl_failure_message(component_frame) if component_frame else "no response"
        )
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


def read_tag(
    host: str,
    area: str,
    byte_offset: int,
    data_type: str,
    port: int = 102,
    db_number: int = 0,
    bit_offset: int = 0,
    timeout: float = 5.0,
) -> dict:
    """Reads a single process-data value via S7comm's "Read Var"
    service -- e.g. DB5.DBW10 (area="DB", db_number=5, byte_offset=10,
    data_type="WORD") or M0.3 (area="M", byte_offset=0, bit_offset=3,
    data_type="BIT"). Read-only: no Write Var support by design."""
    host = (host or "").strip()
    if not host:
        return {"ok": False, "message": "host is required"}
    if not (1 <= port <= 65535):
        return {"ok": False, "message": "port must be 1-65535"}
    area = (area or "").strip().upper()
    if area not in _S7_AREA_CODES:
        return {"ok": False, "message": f"area must be one of {', '.join(_S7_AREA_CODES)}"}
    data_type = (data_type or "").strip().upper()
    if data_type not in _S7_TYPE_SIZES:
        return {"ok": False, "message": f"type must be one of {', '.join(_S7_TYPE_SIZES)}"}
    if area == "DB" and db_number < 1:
        return {"ok": False, "message": "db_number is required (>= 1) when area is DB"}
    if byte_offset < 0:
        return {"ok": False, "message": "byte_offset must be >= 0"}
    if data_type == "BIT" and not (0 <= bit_offset <= 7):
        return {"ok": False, "message": "bit_offset must be 0-7"}

    source_ip = _eth0_source_ip()
    if not source_ip:
        return {
            "ok": False,
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
                "message": "COTP connection not confirmed -- not an S7-compatible device, "
                           "or a different rack/slot than the two defaults tried",
                "response_time_ms": elapsed_ms(),
            }

        sock.sendall(_SETUP_COMMUNICATION)
        setup_response = _recv_tpkt_frame(sock)
        if setup_response is None or len(setup_response) < 8 or setup_response[7] != _S7_PROTOCOL_ID:
            return {
                "ok": False,
                "message": "no valid S7comm response to Setup Communication",
                "response_time_ms": elapsed_ms(),
            }

        request = _build_read_var_request(
            _S7_AREA_CODES[area], db_number, byte_offset, bit_offset, data_type
        )
        sock.sendall(request)
        response = _recv_tpkt_frame(sock)
    except socket.timeout:
        return {"ok": False, "message": "timeout -- no response from device", "response_time_ms": elapsed_ms()}
    except ConnectionRefusedError:
        return {"ok": False, "message": f"connection refused -- port {port} not open",
                 "response_time_ms": elapsed_ms()}
    except OSError as exc:
        return {"ok": False, "message": str(exc), "response_time_ms": elapsed_ms()}
    finally:
        if sock is not None:
            sock.close()

    if response is None:
        return {"ok": False, "message": "no response to Read Var request", "response_time_ms": elapsed_ms()}

    raw, error = _parse_read_var_response(response, _S7_TYPE_SIZES[data_type])
    if error is not None:
        return {"ok": False, "message": error, "response_time_ms": elapsed_ms()}

    return {
        "ok": True,
        "value": _decode_tag_value(data_type, raw),
        "area": area,
        "db_number": db_number if area == "DB" else None,
        "byte_offset": byte_offset,
        "bit_offset": bit_offset if data_type == "BIT" else None,
        "type": data_type,
        "raw_hex": raw.hex(),
        "response_time_ms": elapsed_ms(),
    }
