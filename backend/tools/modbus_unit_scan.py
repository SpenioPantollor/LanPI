"""Modbus Unit ID scanner (Modbus expansion #3).

Probes a range of Modbus unit IDs against one host -- useful when
LanPi is connected to a Modbus TCP-to-RTU gateway serving multiple RTU
devices behind different unit IDs, or just to find which unit ID(s) a
single device actually responds on. Probes with a fixed FC3 (read
holding registers) request at address 0, one register -- the most
widely implemented read function, so a "no response" reading means the
unit ID itself isn't there, not that this specific function happened
to be unsupported.

Conservative and sequential by design: one probe at a time with a
short pause between them, not a burst against the target. A Modbus
exception still counts as "responding" -- it proves a device received
and processed the frame, even though it rejected this specific probe
(modbus.read()'s message distinguishes an exception response from a
real timeout/no-response).

Runs as a background task (mirrors ping.py/mtr.py's start/status/stop
shape), but as a plain Python thread over repeated modbus.read() calls
rather than a subprocess -- modbus.py is already a pure socket client,
nothing to shell out to here.
"""

from __future__ import annotations

import threading
import time

from backend.tools import modbus

_MAX_UNIT_ID = 255
_PROBE_DELAY_SECONDS = 0.1
_PROBE_FUNCTION_CODE = 3  # holding registers -- most widely implemented read function

_lock = threading.Lock()
_state = {
    "running": False,
    "host": None,
    "start_unit": None,
    "end_unit": None,
    "scan_id": 0,
    "results": [],
    "progress": 0,
    "total": 0,
}


def _classify(result: dict) -> tuple[str, str]:
    if result.get("ok"):
        return "responding", "FC3 supported"
    message = result.get("message", "") or ""
    if message.startswith("Modbus exception"):
        return "responding", message
    return "no_response", message


def _run_scan(host: str, start_unit: int, end_unit: int, port: int, timeout: float, scan_id: int) -> None:
    for unit_id in range(start_unit, end_unit + 1):
        with _lock:
            if _state["scan_id"] != scan_id:
                return

        result = modbus.read(host, unit_id, _PROBE_FUNCTION_CODE, 0, 1, port, timeout)
        status, detail = _classify(result)

        with _lock:
            if _state["scan_id"] != scan_id:
                return
            _state["results"].append({"unit_id": unit_id, "status": status, "detail": detail})
            _state["progress"] += 1

        time.sleep(_PROBE_DELAY_SECONDS)

    with _lock:
        if _state["scan_id"] == scan_id:
            _state["running"] = False


def start(host: str, start_unit: int, end_unit: int, port: int = 502, timeout: float = 1.0) -> dict:
    host = (host or "").strip()
    if not host:
        return {"ok": False, "message": "host is required"}
    if not (0 <= start_unit <= _MAX_UNIT_ID) or not (0 <= end_unit <= _MAX_UNIT_ID):
        return {"ok": False, "message": f"unit IDs must be 0-{_MAX_UNIT_ID}"}
    if start_unit > end_unit:
        return {"ok": False, "message": "start unit ID must be <= end unit ID"}
    if not (1 <= port <= 65535):
        return {"ok": False, "message": "port must be 1-65535"}
    if not (0.1 <= timeout <= 10.0):
        return {"ok": False, "message": "timeout must be 0.1-10 seconds"}

    with _lock:
        scan_id = _state["scan_id"] + 1
        _state.update(
            {
                "running": True,
                "host": host,
                "start_unit": start_unit,
                "end_unit": end_unit,
                "scan_id": scan_id,
                "results": [],
                "progress": 0,
                "total": end_unit - start_unit + 1,
            }
        )

    threading.Thread(
        target=_run_scan, args=(host, start_unit, end_unit, port, timeout, scan_id), daemon=True
    ).start()
    return {"ok": True}


def status() -> dict:
    with _lock:
        return {
            "running": _state["running"],
            "host": _state["host"],
            "start_unit": _state["start_unit"],
            "end_unit": _state["end_unit"],
            "results": list(_state["results"]),
            "progress": _state["progress"],
            "total": _state["total"],
        }


def stop() -> dict:
    with _lock:
        if _state["running"]:
            _state["scan_id"] += 1  # the running thread notices on its next check and exits
            _state["running"] = False
            return {"ok": True, "message": "scan stopped"}
        return {"ok": True, "message": "no scan running"}
