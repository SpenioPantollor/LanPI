"""Tests for backend/tools/modbus_templates.py: float32 decoding and
read_template()'s per-register result shape. Uses a temporary templates
file (monkeypatched path) rather than the real config/modbus_templates.json,
so this doesn't depend on -- or accidentally validate -- real device data.
"""
import json
import struct

import pytest

from backend.tools import modbus_templates


def test_decode_float32_combines_two_registers():
    # 78.5 as IEEE-754 float32, split into two big-endian 16-bit halves
    raw = struct.pack("!f", 78.5)
    high, low = struct.unpack("!HH", raw)
    assert modbus_templates._decode("float32", [high, low]) == pytest.approx(78.5)


def test_decode_returns_none_for_unknown_data_type():
    assert modbus_templates._decode(None, [1, 2]) is None
    assert modbus_templates._decode("uint32", [1, 2]) is None


def test_decode_returns_none_when_register_count_mismatches():
    assert modbus_templates._decode("float32", [1]) is None


@pytest.fixture
def temp_templates(tmp_path, monkeypatch):
    path = tmp_path / "modbus_templates.json"
    path.write_text(json.dumps({
        "templates": [
            {
                "id": "test-device",
                "name": "Test Device",
                "unit_id": 5,
                "registers": [
                    {"label": "Reg A", "function_code": 3, "address": 0, "quantity": 1},
                ],
            }
        ]
    }))
    monkeypatch.setattr(modbus_templates, "_TEMPLATES_PATH", path)
    return path


def test_list_templates_reads_from_configured_path(temp_templates):
    templates = modbus_templates.list_templates()
    assert len(templates) == 1
    assert templates[0]["id"] == "test-device"


def test_read_template_unknown_id_returns_not_found(temp_templates):
    result = modbus_templates.read_template("does-not-exist", "1.2.3.4")
    assert result == {"ok": False, "message": "template not found"}


def test_read_template_uses_templates_unit_id(temp_templates, monkeypatch):
    captured = {}

    def fake_read(host, unit_id, function_code, address, quantity, port):
        captured["unit_id"] = unit_id
        return {"ok": True, "values": [42]}

    monkeypatch.setattr(modbus_templates.modbus, "read", fake_read)
    result = modbus_templates.read_template("test-device", "1.2.3.4")

    assert captured["unit_id"] == 5  # from the template, not modbus.read()'s own default
    assert result["ok"] is True
    assert result["results"][0]["label"] == "Reg A"
    assert result["results"][0]["values"] == [42]
