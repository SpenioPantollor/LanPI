"""Live Modbus register polling with communication statistics (Modbus
expansion #7).

Extends the existing single-shot read() with a controlled background
polling loop: reads the same register(s) on a fixed interval, tracking
the running value alongside request/timeout/exception counts and
response-time min/avg/max -- useful for spotting an unstable or
overloaded device/link that a single read wouldn't reveal.

Runs as a background task (mirrors ping.py/mtr.py's start/status/stop
shape) -- a plain Python thread, not a subprocess. Stopping (or the
service restarting, which kills the whole process and every thread in
it) always ends the loop; there's no detached/orphan-process risk here
the way there is for mtr/nmap's child processes, since this never
shells out.
"""

from __future__ import annotations

import threading
import time

from backend.tools import modbus

_MIN_INTERVAL_MS = 200  # floor -- keeps this from becoming an accidental flood
_MAX_INTERVAL_MS = 60_000

_lock = threading.Lock()
_state = {
    "running": False,
    "host": None,
    "port": None,
    "unit_id": None,
    "function_code": None,
    "address": None,
    "quantity": None,
    "interval_ms": None,
    "session_id": 0,
    "value": None,
    "values": None,
    "last_response_time_ms": None,
    "last_message": None,
    "requests": 0,
    "successful": 0,
    "timeouts": 0,
    "exceptions": 0,
    "min_ms": None,
    "avg_ms": None,
    "max_ms": None,
    "_sum_ms": 0.0,
    "_ms_count": 0,
}


def _poll_loop(
    host: str, port: int, unit_id: int, function_code: int, address: int, quantity: int,
    interval_ms: int, session_id: int,
) -> None:
    interval_seconds = interval_ms / 1000
    while True:
        with _lock:
            if _state["session_id"] != session_id:
                return

        result = modbus.read(host, unit_id, function_code, address, quantity, port)

        with _lock:
            if _state["session_id"] != session_id:
                return
            _state["requests"] += 1
            response_time = result.get("response_time_ms")
            _state["last_response_time_ms"] = response_time
            _state["last_message"] = result.get("message")
            if response_time is not None:
                _state["_sum_ms"] += response_time
                _state["_ms_count"] += 1
                _state["min_ms"] = response_time if _state["min_ms"] is None else min(_state["min_ms"], response_time)
                _state["max_ms"] = response_time if _state["max_ms"] is None else max(_state["max_ms"], response_time)
                _state["avg_ms"] = round(_state["_sum_ms"] / _state["_ms_count"], 1)
            if result.get("ok"):
                _state["successful"] += 1
                _state["values"] = result.get("values")
                _state["value"] = result["values"][0] if result.get("values") else None
            else:
                message = result.get("message", "") or ""
                if message.startswith("Modbus exception"):
                    _state["exceptions"] += 1
                elif "timeout" in message:
                    _state["timeouts"] += 1

        time.sleep(interval_seconds)


def start(
    host: str, port: int, unit_id: int, function_code: int, address: int, quantity: int,
    interval_ms: int = 1000,
) -> dict:
    host = (host or "").strip()
    if not host:
        return {"ok": False, "message": "host is required"}
    if function_code not in (1, 2, 3, 4):
        return {"ok": False, "message": "function code must be 1-4"}
    if not (0 <= unit_id <= 255):
        return {"ok": False, "message": "unit ID must be 0-255"}
    if not (0 <= address <= 0xFFFF):
        return {"ok": False, "message": "address must be 0-65535"}
    if not (1 <= quantity <= 125):
        return {"ok": False, "message": "quantity must be 1-125"}
    if not (1 <= port <= 65535):
        return {"ok": False, "message": "port must be 1-65535"}
    if not (_MIN_INTERVAL_MS <= interval_ms <= _MAX_INTERVAL_MS):
        return {"ok": False, "message": f"interval must be {_MIN_INTERVAL_MS}-{_MAX_INTERVAL_MS} ms"}

    with _lock:
        session_id = _state["session_id"] + 1
        _state.update(
            {
                "running": True,
                "host": host,
                "port": port,
                "unit_id": unit_id,
                "function_code": function_code,
                "address": address,
                "quantity": quantity,
                "interval_ms": interval_ms,
                "session_id": session_id,
                "value": None,
                "values": None,
                "last_response_time_ms": None,
                "last_message": None,
                "requests": 0,
                "successful": 0,
                "timeouts": 0,
                "exceptions": 0,
                "min_ms": None,
                "avg_ms": None,
                "max_ms": None,
                "_sum_ms": 0.0,
                "_ms_count": 0,
            }
        )

    threading.Thread(
        target=_poll_loop,
        args=(host, port, unit_id, function_code, address, quantity, interval_ms, session_id),
        daemon=True,
    ).start()
    return {"ok": True}


def status() -> dict:
    with _lock:
        failure_percent = None
        if _state["requests"] > 0:
            failure_percent = round(100 * (_state["requests"] - _state["successful"]) / _state["requests"], 1)
        return {
            "running": _state["running"],
            "host": _state["host"],
            "port": _state["port"],
            "unit_id": _state["unit_id"],
            "function_code": _state["function_code"],
            "address": _state["address"],
            "quantity": _state["quantity"],
            "interval_ms": _state["interval_ms"],
            "value": _state["value"],
            "values": _state["values"],
            "last_response_time_ms": _state["last_response_time_ms"],
            "last_message": _state["last_message"],
            "requests": _state["requests"],
            "successful": _state["successful"],
            "timeouts": _state["timeouts"],
            "exceptions": _state["exceptions"],
            "failure_percent": failure_percent,
            "min_ms": _state["min_ms"],
            "avg_ms": _state["avg_ms"],
            "max_ms": _state["max_ms"],
        }


def stop() -> dict:
    with _lock:
        if _state["running"]:
            _state["session_id"] += 1
            _state["running"] = False
            return {"ok": True, "message": "polling stopped"}
        return {"ok": True, "message": "no polling running"}
