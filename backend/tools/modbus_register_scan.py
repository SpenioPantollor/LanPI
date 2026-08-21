"""Modbus register range scanner (Modbus expansion #4).

Finds which addresses in a range are actually readable on a device
with no register map to go on. Probes in as few requests as possible:
a whole block at once (up to the function's own max quantity), only
bisecting (in half, not one register at a time) when a block fails, to
narrow down exactly where a readable/unreadable boundary sits. A
device with mostly-readable or mostly-unreadable space gets scanned in
O(log n) requests instead of O(n); only genuinely patchy address space
costs more, and even then every probe still reads as large a block as
it safely can rather than falling back to single-register requests
across the board.

A non-exception failure (timeout, connection refused) short-circuits
the bisection immediately -- retrying smaller pieces of a
non-responding link tells us nothing a real communication failure
didn't already, so the whole remaining block is reported unreadable
with that message rather than probing it piece by piece for no reason.

Runs as a background task (mirrors ping.py/mtr.py's start/status/stop
shape), a plain Python thread over repeated modbus.read() calls (no
subprocess -- same as modbus_unit_scan.py).
"""

from __future__ import annotations

import threading
import time

from backend.tools import modbus

_MAX_RANGE_SIZE = 10000  # refuse to scan more than this many addresses in one go
_PROBE_DELAY_SECONDS = 0.05
_MAX_QUANTITY = {1: 2000, 2: 2000, 3: 125, 4: 125}
_REGISTER_TYPES = {
    "coils": 1,
    "discrete_inputs": 2,
    "holding_registers": 3,
    "input_registers": 4,
}

_lock = threading.Lock()
_state = {
    "running": False,
    "host": None,
    "register_type": None,
    "unit_id": None,
    "start_address": None,
    "end_address": None,
    "scan_id": 0,
    "segments": [],
    "progress": 0,
    "total": 0,
}


def _bump_progress(scan_id: int, count: int) -> None:
    with _lock:
        if _state["scan_id"] == scan_id:
            _state["progress"] += count


def _stopped(scan_id: int) -> bool:
    with _lock:
        return _state["scan_id"] != scan_id


def _probe(
    host: str, unit_id: int, function_code: int, start: int, end: int,
    port: int, timeout: float, scan_id: int,
) -> list[dict]:
    if _stopped(scan_id):
        return [{"start": start, "end": end, "readable": None, "message": "stopped"}]

    quantity = end - start + 1
    max_quantity = _MAX_QUANTITY[function_code]

    if quantity > max_quantity:
        segments = []
        pos = start
        while pos <= end:
            chunk_end = min(pos + max_quantity - 1, end)
            segments += _probe(host, unit_id, function_code, pos, chunk_end, port, timeout, scan_id)
            pos = chunk_end + 1
        return segments

    result = modbus.read(host, unit_id, function_code, start, quantity, port, timeout)
    time.sleep(_PROBE_DELAY_SECONDS)

    if result.get("ok"):
        _bump_progress(scan_id, quantity)
        return [{"start": start, "end": end, "readable": True, "message": None}]

    message = result.get("message", "") or ""
    if quantity == 1 or not message.startswith("Modbus exception"):
        _bump_progress(scan_id, quantity)
        return [{"start": start, "end": end, "readable": False, "message": message}]

    mid = start + quantity // 2
    left = _probe(host, unit_id, function_code, start, mid - 1, port, timeout, scan_id)
    right = _probe(host, unit_id, function_code, mid, end, port, timeout, scan_id)
    return left + right


def _merge_segments(segments: list[dict]) -> list[dict]:
    """Merge adjacent segments sharing the same outcome, so two
    separately-probed but identically-readable blocks show up as one
    row instead of an implementation-detail-driven split."""
    if not segments:
        return []
    ordered = sorted(segments, key=lambda s: s["start"])
    merged = [dict(ordered[0])]
    for seg in ordered[1:]:
        last = merged[-1]
        if seg["start"] == last["end"] + 1 and seg["readable"] == last["readable"] and seg["message"] == last["message"]:
            last["end"] = seg["end"]
        else:
            merged.append(dict(seg))
    return merged


def _run_scan(
    host: str, unit_id: int, function_code: int, start_address: int, end_address: int,
    port: int, timeout: float, scan_id: int,
) -> None:
    segments = _probe(host, unit_id, function_code, start_address, end_address, port, timeout, scan_id)
    merged = _merge_segments(segments)
    with _lock:
        if _state["scan_id"] != scan_id:
            return
        _state["segments"] = merged
        _state["running"] = False


def start(
    host: str, register_type: str, unit_id: int, start_address: int, end_address: int,
    port: int = 502, timeout: float = 1.0,
) -> dict:
    host = (host or "").strip()
    if not host:
        return {"ok": False, "message": "host is required"}
    function_code = _REGISTER_TYPES.get(register_type)
    if function_code is None:
        return {"ok": False, "message": f"register type must be one of {', '.join(_REGISTER_TYPES)}"}
    if not (0 <= unit_id <= 255):
        return {"ok": False, "message": "unit ID must be 0-255"}
    if not (0 <= start_address <= 0xFFFF) or not (0 <= end_address <= 0xFFFF):
        return {"ok": False, "message": "addresses must be 0-65535"}
    if start_address > end_address:
        return {"ok": False, "message": "start address must be <= end address"}
    if end_address - start_address + 1 > _MAX_RANGE_SIZE:
        return {"ok": False, "message": f"range too large -- max {_MAX_RANGE_SIZE} addresses per scan"}
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
                "register_type": register_type,
                "unit_id": unit_id,
                "start_address": start_address,
                "end_address": end_address,
                "scan_id": scan_id,
                "segments": [],
                "progress": 0,
                "total": end_address - start_address + 1,
            }
        )

    threading.Thread(
        target=_run_scan,
        args=(host, unit_id, function_code, start_address, end_address, port, timeout, scan_id),
        daemon=True,
    ).start()
    return {"ok": True}


def status() -> dict:
    with _lock:
        return {
            "running": _state["running"],
            "host": _state["host"],
            "register_type": _state["register_type"],
            "unit_id": _state["unit_id"],
            "start_address": _state["start_address"],
            "end_address": _state["end_address"],
            "segments": list(_state["segments"]),
            "progress": _state["progress"],
            "total": _state["total"],
        }


def stop() -> dict:
    with _lock:
        if _state["running"]:
            _state["scan_id"] += 1
            _state["running"] = False
            return {"ok": True, "message": "scan stopped"}
        return {"ok": True, "message": "no scan running"}
