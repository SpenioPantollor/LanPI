"""MTR (combined traceroute + ping per hop) on the TEST PORT (eth0).

Uses mtr's own --json report mode rather than scraping text output --
much more reliable than parsing mtr's human-readable table. Sourced
from eth0's current address (via -a), same reasoning as tcp_test.py:
eth0 has no default route by design, so an unbound run would silently
trace out wlan0 instead for any target outside eth0's local subnet.
Requires DHCP/Static mode -- Passive has no source address to bind.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from backend.network import eth0_mode

_MTR_CANDIDATES = ["/usr/bin/mtr", "/usr/sbin/mtr", "mtr"]
_MAX_CYCLES = 60


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


def run(host: str, cycles: int = 10) -> dict:
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
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=cycles + 30)
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "mtr timed out"}
    except OSError as exc:
        return {"ok": False, "message": str(exc)}

    if result.returncode != 0:
        return {"ok": False, "message": (result.stderr or result.stdout).strip()}

    try:
        data = json.loads(result.stdout)
    except ValueError:
        return {"ok": False, "message": "could not parse mtr output"}

    report = data.get("report", {})
    hubs = report.get("hubs", [])
    hops = [
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
    return {"ok": True, "host": host, "cycles": cycles, "hops": hops}
