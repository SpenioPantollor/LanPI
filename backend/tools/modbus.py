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

Sourced from eth0's current address (socket bind), same reasoning as
tcp_test.py: eth0 has no default route by design, so an unbound
connect() to a host outside eth0's subnet would silently go out wlan0
instead.
"""

from __future__ import annotations

import socket
import struct
import threading

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

_transaction_lock = threading.Lock()
_transaction_id = 0


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
    try:
        sock.bind((source_ip, 0))
        sock.connect((host, port))
        sock.sendall(request)

        header = _recv_exact(sock, 7)
        if header is None:
            return {"ok": False, "message": "no response (connection closed)"}
        resp_transaction_id, _resp_protocol_id, resp_length, _resp_unit_id = struct.unpack("!HHHB", header)
        if resp_transaction_id != transaction_id:
            return {"ok": False, "message": "unexpected transaction ID in response"}

        body = _recv_exact(sock, resp_length - 1)
        if body is None or len(body) < 1:
            return {"ok": False, "message": "incomplete response"}
    except socket.timeout:
        return {"ok": False, "message": "timeout -- no response from device"}
    except ConnectionRefusedError:
        return {"ok": False, "message": f"connection refused -- port {port} not open"}
    except OSError as exc:
        return {"ok": False, "message": str(exc)}
    finally:
        sock.close()

    resp_function_code = body[0]
    if resp_function_code & 0x80:
        exception_code = body[1] if len(body) > 1 else None
        return {
            "ok": False,
            "message": f"Modbus exception: {_EXCEPTION_MESSAGES.get(exception_code, f'code {exception_code}')}",
        }
    if resp_function_code != function_code:
        return {"ok": False, "message": "unexpected function code in response"}
    if len(body) < 2:
        return {"ok": False, "message": "malformed response"}

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
    }
