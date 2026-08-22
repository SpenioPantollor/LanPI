"""Modbus TCP client (read functions only) on the TEST PORT (eth0).

Hand-rolled Modbus TCP -- no pymodbus dependency, consistent with this
project's LLDP/CDP/MNDP parsers: Modbus TCP's MBAP header + PDU is a
simple enough binary format not to need a library for just the four
read functions.

Read-only by design (function codes 1-4: coils, discrete inputs,
holding registers, input registers). This project defaults to
passive/non-disruptive operation (ARCHITECTURE.MD's Safety section) --
write functions would let this tool modify a live industrial device's
outputs, out of scope unless explicitly requested.

Sourced from eth0's current address (socket bind) AND the eth0 device
itself (SO_BINDTODEVICE), same reasoning as tcp_test.py: an
address-only bind isn't enough when eth0 and wlan0 both have a route
to the same subnet (this dev rig's eth0 test switch uplinks into the
same LAN as wlan0) -- Linux's weak-host-model routing then picks
whichever interface has the lower metric for the destination
regardless of the bound source address, so requests can silently go
out wlan0 while still claiming eth0's source IP. Confirmed live
2026-08-22: the passive Modbus traffic analyzer (backend/capture/
modbus_traffic.py), which only watches eth0, never saw a single
request despite reads succeeding -- parallel tcpdump on both
interfaces during a real read showed the request on wlan0, only the
response on eth0. SO_BINDTODEVICE closes that gap by forcing the
actual physical interface; needs CAP_NET_RAW, granted to
lanpi.service via AmbientCapabilities.
"""

from __future__ import annotations

import socket
import struct
import threading
import time

from backend.network import eth0_mode

_READ_FUNCTIONS = {
    1: "read_coils",
    2: "read_discrete_inputs",
    3: "read_holding_registers",
    4: "read_input_registers",
}
_MAX_QUANTITY = {
    1: 2000,  # coils/discrete inputs -- Modbus spec limit for a single read
    2: 2000,
    3: 125,   # holding/input registers
    4: 125,
}
_EXCEPTION_MESSAGES = {
    1: "Illegal Function",
    2: "Illegal Data Address",
    3: "Illegal Data Value",
    4: "Slave Device Failure",
    5: "Acknowledge",
    6: "Slave Device Busy",
    8: "Memory Parity Error",
    10: "Gateway Path Unavailable",
    11: "Gateway Target Device Failed to Respond",
}
_DEVICE_ID_OBJECT_NAMES = {
    0x00: "vendor_name",
    0x01: "product_code",
    0x02: "major_minor_revision",
    0x03: "vendor_url",
    0x04: "product_name",
    0x05: "model_name",
    0x06: "user_application_name",
}

_transaction_lock = threading.Lock()
_transaction_id = 0


def _bind_to_eth0_device(sock: socket.socket) -> None:
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"eth0")
    except (OSError, AttributeError):
        # OSError: no CAP_NET_RAW. AttributeError: SO_BINDTODEVICE
        # doesn't exist on this platform (e.g. macOS, used for local
        # dev testing -- it's Linux-only). Either way, falls back to
        # address-only binding.
        pass


def _next_transaction_id() -> int:
    global _transaction_id
    with _transaction_lock:
        _transaction_id = (_transaction_id + 1) % 0x10000
        return _transaction_id


def _eth0_source_ip() -> str | None:
    mode = eth0_mode.get_mode()
    address = mode.get("address")
    return address.split("/")[0] if address else None


def _recv_exact(sock: socket.socket, count: int) -> bytes | None:
    data = b""
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def read(
    host: str,
    unit_id: int,
    function_code: int,
    address: int,
    quantity: int,
    port: int = 502,
    timeout: float = 3.0,
) -> dict:
    host = (host or "").strip()
    if not host:
        return {"ok": False, "message": "host is required"}
    if function_code not in _READ_FUNCTIONS:
        return {
            "ok": False,
            "message": "function code must be 1 (coils), 2 (discrete inputs), "
                       "3 (holding registers), or 4 (input registers)",
        }
    if not (0 <= unit_id <= 255):
        return {"ok": False, "message": "unit ID must be 0-255"}
    if not (0 <= address <= 0xFFFF):
        return {"ok": False, "message": "address must be 0-65535"}
    max_quantity = _MAX_QUANTITY[function_code]
    if not (1 <= quantity <= max_quantity):
        return {"ok": False, "message": f"quantity must be 1-{max_quantity} for this function"}
    if not (1 <= port <= 65535):
        return {"ok": False, "message": "port must be 1-65535"}

    source_ip = _eth0_source_ip()
    if not source_ip:
        return {
            "ok": False,
            "message": "eth0 has no IP address -- switch to DHCP or Static mode first "
                       "(Passive mode has no source address to connect from)",
        }

    transaction_id = _next_transaction_id()
    pdu = struct.pack("!BHH", function_code, address, quantity)
    mbap = struct.pack("!HHHB", transaction_id, 0, len(pdu) + 1, unit_id)
    request = mbap + pdu

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    started = time.monotonic()
    header = None
    body = None
    try:
        _bind_to_eth0_device(sock)
        sock.bind((source_ip, 0))
        sock.connect((host, port))
        sock.sendall(request)

        header = _recv_exact(sock, 7)
        if header is None:
            return _read_error("no response (connection closed)", request, started)
        resp_transaction_id, _resp_protocol_id, resp_length, _resp_unit_id = struct.unpack("!HHHB", header)
        if resp_transaction_id != transaction_id:
            return _read_error("unexpected transaction ID in response", request, started, header)

        body = _recv_exact(sock, resp_length - 1)
        if body is None or len(body) < 1:
            return _read_error("incomplete response", request, started, header, body)
    except socket.timeout:
        return _read_error("timeout -- no response from device", request, started, header, body)
    except ConnectionRefusedError:
        return _read_error(f"connection refused -- port {port} not open", request, started, header, body)
    except OSError as exc:
        return _read_error(str(exc), request, started, header, body)
    finally:
        sock.close()

    response_time_ms = round((time.monotonic() - started) * 1000, 1)
    raw_request = request.hex()
    raw_response = (header + body).hex()

    resp_function_code = body[0]
    if resp_function_code & 0x80:
        exception_code = body[1] if len(body) > 1 else None
        return {
            "ok": False,
            "message": f"Modbus exception: {_EXCEPTION_MESSAGES.get(exception_code, f'code {exception_code}')}",
            "response_time_ms": response_time_ms,
            "raw_request": raw_request,
            "raw_response": raw_response,
        }
    if resp_function_code != function_code:
        return _read_error("unexpected function code in response", request, started, header, body)
    if len(body) < 2:
        return _read_error("malformed response", request, started, header, body)

    byte_count = body[1]
    data = body[2:2 + byte_count]

    if function_code in (1, 2):
        values = [bool(data[i // 8] & (1 << (i % 8))) for i in range(quantity) if i // 8 < len(data)]
    else:
        values = [struct.unpack("!H", data[i:i + 2])[0] for i in range(0, len(data) - 1, 2)]

    return {
        "ok": True,
        "function": _READ_FUNCTIONS[function_code],
        "address": address,
        "quantity": quantity,
        "values": values,
        "response_time_ms": response_time_ms,
        "raw_request": raw_request,
        "raw_response": raw_response,
    }


def _read_error(
    message: str,
    request: bytes | None,
    started: float,
    header: bytes | None = None,
    body: bytes | None = None,
) -> dict:
    """Shared error-return shape for read()/read_device_identification()
    once a request has actually gone out on the wire -- always includes
    timing and whatever raw bytes were actually seen (even a partial or
    malformed response is useful in the raw request/response view),
    unlike the pure-input-validation returns earlier in each function,
    which never touched the network and so have nothing to show.
    `request` is None for read_device_identification() specifically when
    connect()/bind() itself fails before its first iteration ever builds
    a request (unlike read(), which always builds its one-shot request
    before opening the socket)."""
    raw_response = None
    if header is not None:
        raw_response = (header + (body or b"")).hex()
    return {
        "ok": False,
        "message": message,
        "response_time_ms": round((time.monotonic() - started) * 1000, 1),
        "raw_request": request.hex() if request is not None else None,
        "raw_response": raw_response,
    }


def read_device_identification(host: str, unit_id: int, port: int = 502, timeout: float = 3.0) -> dict:
    """Modbus Read Device Identification (FC43 / MEI type 14).

    Not every device supports this -- a device that doesn't returns an
    Illegal Function exception, same as any other unsupported function
    code. That's reported as {"ok": False, "supported": False, ...}
    rather than a generic communication failure, so the UI can show
    "not supported" instead of implying something's wrong with the
    connection.

    Requests "regular" access (read device ID code 2), which asks for
    the fuller object set (vendor URL/product name/model name/user app
    name, in addition to vendor name/product code/revision). Devices
    that only implement "basic" access still respond fine -- they just
    return the smaller object set they actually have, reflected in the
    response's own conformity level/more-follows fields, which this
    follows to collect every object across as many requests as the
    device says are needed (object IDs are exposed as their standard
    names where known, e.g. "vendor_name", falling back to
    "object_<n>" for vendor-specific extended objects).
    """
    host = (host or "").strip()
    if not host:
        return {"ok": False, "message": "host is required"}
    if not (0 <= unit_id <= 255):
        return {"ok": False, "message": "unit ID must be 0-255"}
    if not (1 <= port <= 65535):
        return {"ok": False, "message": "port must be 1-65535"}

    source_ip = _eth0_source_ip()
    if not source_ip:
        return {
            "ok": False,
            "message": "eth0 has no IP address -- switch to DHCP or Static mode first "
                       "(Passive mode has no source address to connect from)",
        }

    objects: dict[str, str] = {}
    object_id = 0x00
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    started = time.monotonic()
    request = None  # only ever built inside the loop below -- stays None if
    # bind()/connect() itself fails first, so the except handlers below
    # have something valid to pass to _read_error() either way
    try:
        _bind_to_eth0_device(sock)
        sock.bind((source_ip, 0))
        sock.connect((host, port))

        # A misbehaving device could set more-follows forever without
        # actually advancing next-object-id -- cap iterations so that
        # can't hang this in a loop.
        for _ in range(8):
            transaction_id = _next_transaction_id()
            pdu = struct.pack("!BBBB", 0x2B, 0x0E, 0x02, object_id)
            mbap = struct.pack("!HHHB", transaction_id, 0, len(pdu) + 1, unit_id)
            request = mbap + pdu
            sock.sendall(request)

            header = _recv_exact(sock, 7)
            if header is None:
                return _read_error("no response (connection closed)", request, started)
            resp_transaction_id, _protocol, resp_length, _unit = struct.unpack("!HHHB", header)
            if resp_transaction_id != transaction_id:
                return _read_error("unexpected transaction ID in response", request, started, header)

            body = _recv_exact(sock, resp_length - 1)
            if body is None or len(body) < 1:
                return _read_error("incomplete response", request, started, header, body)

            resp_function_code = body[0]
            if resp_function_code & 0x80:
                exception_code = body[1] if len(body) > 1 else None
                return {
                    "ok": False,
                    "supported": False,
                    "message": "Device Identification not supported ("
                               f"{_EXCEPTION_MESSAGES.get(exception_code, f'code {exception_code}')})",
                    "response_time_ms": round((time.monotonic() - started) * 1000, 1),
                    "raw_request": request.hex(),
                    "raw_response": (header + body).hex(),
                }
            if resp_function_code != 0x2B or len(body) < 7 or body[1] != 0x0E:
                return _read_error("malformed device identification response", request, started, header, body)

            more_follows = body[4]
            next_object_id = body[5]
            number_of_objects = body[6]
            offset = 7
            for _ in range(number_of_objects):
                if offset + 2 > len(body):
                    break
                obj_id = body[offset]
                obj_len = body[offset + 1]
                offset += 2
                obj_value = body[offset:offset + obj_len]
                offset += obj_len
                name = _DEVICE_ID_OBJECT_NAMES.get(obj_id, f"object_{obj_id}")
                objects[name] = obj_value.decode("ascii", errors="replace")

            if more_follows != 0xFF or next_object_id == object_id:
                break
            object_id = next_object_id
    except socket.timeout:
        return _read_error("timeout -- no response from device", request, started)
    except ConnectionRefusedError:
        return _read_error(f"connection refused -- port {port} not open", request, started)
    except OSError as exc:
        return _read_error(str(exc), request, started)
    finally:
        sock.close()

    return {
        "ok": True,
        "supported": True,
        "objects": objects,
        "response_time_ms": round((time.monotonic() - started) * 1000, 1),
    }
