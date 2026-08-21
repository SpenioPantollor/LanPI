"""Register data interpretation helper (Modbus expansion #6).

Given the raw 16-bit register values a read already returned, shows
every representation that might be useful rather than guessing which
one the caller wants -- 16-bit unsigned/signed/hex/binary always, plus
32-bit unsigned/signed/float (under an explicit byte order) once a
second register is available. The byte order is a caller-supplied
parameter, never inferred: silently guessing it would just as often
show a wrong number as a right one, and Modbus has no standard way to
signal which convention a given device uses.
"""

from __future__ import annotations

import math
import struct

BYTE_ORDERS = ("ABCD", "BADC", "CDAB", "DCBA")


def _register_bytes(value: int) -> tuple[int, int]:
    return (value >> 8) & 0xFF, value & 0xFF


def _combine_32bit(reg0: int, reg1: int, byte_order: str) -> bytes:
    """Combine two registers' 4 bytes per the given order -- A/B are
    reg0's high/low byte, C/D are reg1's, matching the conventional
    Modbus float32 naming (ABCD is the "plain big-endian" case: reg0 is
    the high word, and neither register's own byte order is swapped)."""
    a, b = _register_bytes(reg0)
    c, d = _register_bytes(reg1)
    order = {
        "ABCD": (a, b, c, d),
        "BADC": (b, a, d, c),
        "CDAB": (c, d, a, b),
        "DCBA": (d, c, b, a),
    }[byte_order]
    return bytes(order)


def decode_registers(values: list[int], byte_order: str = "ABCD") -> dict:
    if not values:
        return {"error": "no register values given"}
    if byte_order not in BYTE_ORDERS:
        return {"error": f"byte order must be one of {', '.join(BYTE_ORDERS)}"}

    reg0 = values[0] & 0xFFFF
    result: dict = {
        "uint16": reg0,
        "int16": reg0 - 0x10000 if reg0 & 0x8000 else reg0,
        "hex16": f"0x{reg0:04X}",
        "binary16": format(reg0, "016b"),
    }

    if len(values) >= 2:
        reg1 = values[1] & 0xFFFF
        raw32 = _combine_32bit(reg0, reg1, byte_order)
        uint32 = struct.unpack("!I", raw32)[0]
        int32 = struct.unpack("!i", raw32)[0]
        float32 = struct.unpack("!f", raw32)[0]
        result.update(
            {
                "uint32": uint32,
                "int32": int32,
                # NaN/inf are valid float32 bit patterns but never a
                # meaningful reading -- reported as None (not a made-up
                # number) so the UI can show "n/a" instead.
                "float32": float32 if math.isfinite(float32) else None,
                "hex32": f"0x{uint32:08X}",
                "byte_order": byte_order,
            }
        )

    return result
