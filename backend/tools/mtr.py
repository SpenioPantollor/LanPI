"""MTR (combined traceroute + ping per hop) on the TEST PORT (eth0).

Runs as a background process (mirrors ping.py's start/status/stop
design) so a run against an unreachable host can be cancelled instead
of blocking the caller until mtr's own cycle count finishes. Uses
mtr's own --report --json output rather than scraping its text table
-- much more reliable to parse -- but that also means results only
appear all at once when mtr exits normally; a stopped run reports no
hops, just "stopped".

Sourced from eth0's current address (via -a), same reasoning as
tcp_test.py: eth0 has no default route by design, so an unbound run
would silently trace out wlan0 instead for any target outside eth0's
local subnet. Requires DHCP/Static mode -- Passive has no source
address to bind.
"""

from __future__ import annotations

import json
import shutil
import signal
import subprocess
import threading

from backend.network import eth0_mode

_MTR_CANDIDATES = ["/usr/bin/mtr", "/usr/sbin/mtr", "mtr"]
_MAX_CYCLES = 60

_lock = threading.Lock()
_state = {
    "running": False,
    "host": None,
    "cycles": None,
    "process": None,
    "ok": None,
    "message": None,
    "hops": [],
    "_stopped": False,
}


def _find_mtr() -> str | None:
    for candidate in _MTR_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _eth0_source_ip() -> str | None:
    mode = eth0_mode.get_mode()
    address = mode.get("address")
    return address.split("/")[0] if address else None


def _reader_loop(process: subprocess.Popen, host: str) -> None:
    stdout, stderr = process.communicate()
    with _lock:
        if _state["host"] != host:
            return  # superseded by a newer run
        _state["running"] = False

        if _state["_stopped"]:
            _state["ok"] = False
            _state["message"] = "stopped"
            return

        if process.returncode != 0:
            _state["ok"] = False
            _state["message"] = (stderr or stdout or "mtr failed").strip() or "mtr failed"
            return

        try:
            data = json.loads(stdout)
        except ValueError:
            _state["ok"] = False
            _state["message"] = "could not parse mtr output"
            return

        hubs = data.get("report", {}).get("hubs", [])
        _state["hops"] = [
            {
                "hop": hub.get("count"),
                "host": hub.get("host"),
                "loss_percent": hub.get("Loss%"),
                "sent": hub.get("Snt"),
                "last_ms": hub.get("Last"),
                "avg_ms": hub.get("Avg"),
                "best_ms": hub.get("Best"),
                "worst_ms": hub.get("Wrst"),
            }
            for hub in hubs
        ]
        _state["ok"] = True
        _state["message"] = None


def start(host: str, cycles: int = 10) -> dict:
    host = (host or "").strip()
    if not host:
        return {"ok": False, "message": "host is required"}
    if not (1 <= cycles <= _MAX_CYCLES):
        return {"ok": False, "message": f"cycles must be between 1 and {_MAX_CYCLES}"}

    mtr_bin = _find_mtr()
    if not mtr_bin:
        return {"ok": False, "message": "mtr not available"}

    source_ip = _eth0_source_ip()
    if not source_ip:
        return {
            "ok": False,
            "message": "eth0 has no IP address -- switch to DHCP or Static mode first "
                       "(Passive mode has no source address to trace from)",
        }

    args = [mtr_bin, "--report", "--json", "--no-dns", "-a", source_ip, "-c", str(cycles), host]

    with _lock:
        old_process = _state.get("process")
        if old_process is not None and _state["running"]:
            old_process.send_signal(signal.SIGTERM)

        try:
            process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        except OSError as exc:
            return {"ok": False, "message": str(exc)}

        _state.update(
            {
                "running": True,
                "host": host,
                "cycles": cycles,
                "process": process,
                "ok": None,
                "message": None,
                "hops": [],
                "_stopped": False,
            }
        )

    threading.Thread(target=_reader_loop, args=(process, host), daemon=True).start()
    return {"ok": True}


def status() -> dict:
    with _lock:
        return {
            "running": _state["running"],
            "host": _state["host"],
            "cycles": _state["cycles"],
            "ok": _state["ok"],
            "message": _state["message"],
            "hops": _state["hops"],
        }


def stop() -> dict:
    with _lock:
        process = _state.get("process")
        if process is not None and _state["running"]:
            _state["_stopped"] = True
            process.send_signal(signal.SIGTERM)
            return {"ok": True, "message": "mtr stopping"}
        return {"ok": True, "message": "no mtr running"}
