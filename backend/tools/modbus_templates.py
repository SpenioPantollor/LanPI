"""Device templates for Modbus reads -- named presets (unit ID + a
list of labeled registers) so a known device type's function code/
address/quantity don't need re-entering by hand every time.

Templates live in config/modbus_templates.json, tracked in git: a
device's register map (which function/address reads which value) is
public information from its own manufacturer documentation, not
site-specific data -- nothing about a specific Kamstrup or Phoenix
Contact meter model's register layout identifies where or how it's
deployed. config/modbus_templates.example.json (a placeholder with
fake addresses) is used as a fallback if the real file is ever
missing, so the feature still works out of the box.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.tools import modbus

_REPO_DIR = Path(__file__).resolve().parent.parent.parent
_TEMPLATES_PATH = _REPO_DIR / "config" / "modbus_templates.json"
_EXAMPLE_PATH = _REPO_DIR / "config" / "modbus_templates.example.json"


def list_templates() -> list[dict]:
    path = _TEMPLATES_PATH if _TEMPLATES_PATH.exists() else _EXAMPLE_PATH
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return data.get("templates", [])


def _find_template(template_id: str) -> dict | None:
    for template in list_templates():
        if template.get("id") == template_id:
            return template
    return None


def read_template(template_id: str, host: str, port: int = 502) -> dict:
    template = _find_template(template_id)
    if template is None:
        return {"ok": False, "message": "template not found"}

    unit_id = template.get("unit_id", 1)
    results = []
    for reg in template.get("registers", []):
        result = modbus.read(
            host, unit_id, reg.get("function_code"), reg.get("address"),
            reg.get("quantity"), port,
        )
        results.append(
            {
                "label": reg.get("label", ""),
                "note": reg.get("note", ""),
                "function_code": reg.get("function_code"),
                "address": reg.get("address"),
                "quantity": reg.get("quantity"),
                "ok": result.get("ok", False),
                "message": result.get("message"),
                "values": result.get("values", []),
            }
        )

    return {"ok": True, "template": template.get("name"), "results": results}
