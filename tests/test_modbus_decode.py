"""Tests for backend/tools/modbus_decode.py's register interpretation
helper -- 16/32-bit signed/unsigned conversion, hex/binary, and
FLOAT32 under each of the 4 byte-order conventions."""
import struct

from backend.tools import modbus_decode


def test_uint16_and_int16_positive_value():
    result = modbus_decode.decode_registers([1234])
    assert result["uint16"] == 1234
    assert result["int16"] == 1234
    assert result["hex16"] == "0x04D2"
    assert result["binary16"] == "0000010011010010"


def test_int16_negative_value_via_twos_complement():
    result = modbus_decode.decode_registers([0xFFFF])  # -1 as int16
    assert result["uint16"] == 65535
    assert result["int16"] == -1


def test_single_register_has_no_32bit_fields():
    result = modbus_decode.decode_registers([1234])
    assert "uint32" not in result
    assert "float32" not in result


def test_float32_abcd_matches_textbook_example():
    # 12.34 as float32, split into two big-endian registers -- the
    # standard worked example for Modbus ABCD byte order.
    raw = struct.pack("!f", 12.34)
    reg0, reg1 = struct.unpack("!HH", raw)
    result = modbus_decode.decode_registers([reg0, reg1], byte_order="ABCD")
    assert round(result["float32"], 2) == 12.34
    assert result["byte_order"] == "ABCD"


def test_float32_word_swap_cdab():
    # CDAB: registers swapped, bytes within each register untouched --
    # a common convention for meters that transmit the low word first.
    raw = struct.pack("!f", 12.34)
    reg0, reg1 = struct.unpack("!HH", raw)  # this is the ABCD-order pair
    # For CDAB to recover the same float, the wire order must be reg1, reg0.
    result = modbus_decode.decode_registers([reg1, reg0], byte_order="CDAB")
    assert round(result["float32"], 2) == 12.34


def test_float32_byte_swap_badc():
    raw = struct.pack("!f", 12.34)
    a, b, c, d = raw
    swapped = bytes([b, a, d, c])  # BADC of the original
    reg0, reg1 = struct.unpack("!HH", swapped)
    result = modbus_decode.decode_registers([reg0, reg1], byte_order="BADC")
    assert round(result["float32"], 2) == 12.34


def test_float32_full_reverse_dcba():
    raw = struct.pack("!f", 12.34)
    a, b, c, d = raw
    reversed_bytes = bytes([d, c, b, a])
    reg0, reg1 = struct.unpack("!HH", reversed_bytes)
    result = modbus_decode.decode_registers([reg0, reg1], byte_order="DCBA")
    assert round(result["float32"], 2) == 12.34


def test_uint32_and_int32():
    raw = struct.pack("!I", 0x00010002)
    reg0, reg1 = struct.unpack("!HH", raw)
    result = modbus_decode.decode_registers([reg0, reg1])
    assert result["uint32"] == 0x00010002
    assert result["hex32"] == "0x00010002"


def test_int32_negative_via_twos_complement():
    raw = struct.pack("!i", -1)
    reg0, reg1 = struct.unpack("!HH", raw)
    result = modbus_decode.decode_registers([reg0, reg1])
    assert result["uint32"] == 0xFFFFFFFF
    assert result["int32"] == -1


def test_float32_nan_reported_as_none_not_a_fake_number():
    reg0, reg1 = struct.unpack("!HH", struct.pack("!f", float("nan")))
    result = modbus_decode.decode_registers([reg0, reg1])
    assert result["float32"] is None


def test_rejects_empty_values():
    result = modbus_decode.decode_registers([])
    assert "error" in result


def test_rejects_unknown_byte_order():
    result = modbus_decode.decode_registers([1, 2], byte_order="WXYZ")
    assert "error" in result
