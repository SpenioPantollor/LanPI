"""TCP/UDP port-range scan on a single host, via nmap.

Complementary to tcp_test.py's single-port check: scans a range at
once. Uses nmap for the same reason as ip_scanner.py -- far faster and
more mature than hand-rolling concurrent socket probes, and the
sudo+process-group infrastructure is already proven there. SYN scan
(-sS) needs real root the same way ip_scanner's MAC-address discovery
does, so this goes through sudo the same way rather than setcap.

-Pn skips nmap's own host-discovery ping and treats the target as up
regardless -- plenty of real devices (embedded/industrial included)
don't answer ICMP but do have open TCP ports, and without -Pn nmap
would silently report the host as down and scan nothing.

Runs as a background process (mirrors mtr.py). Unlike ip_scanner's
per-host discovery, nmap's port-scan results only appear as a single
table once the scan finishes (not streamed port-by-port), so this
looks like mtr.py's "nothing until done" shape -- stoppable mid-scan
for the same reason: a big range or an unresponsive host can take a
while.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading

from backend import shell
from backend.network import eth0_mode

_NMAP_CANDIDATES = ["/usr/bin/nmap", "/usr/sbin/nmap", "nmap"]
_SUDO_CANDIDATES = ["/usr/bin/sudo", "/bin/sudo", "sudo"]
_PORT_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")
_PORT_LINE_RE = re.compile(r"^(\d+)/(tcp|udp)\s+(\S+)\s+(\S+)")

_lock = threading.Lock()
_state = {
    "running": False,
    "host": None,
    "port_range": None,
    "scan_id": 0,
    "process": None,
    "ok": None,
    "message": None,
    "ports": [],
    "_stopped": False,
}


def _eth0_source_ip() -> str | None:
    mode = eth0_mode.get_mode()
    address = mode.get("address")
    return address.split("/")[0] if address else None


def _terminate(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _parse_port_range(port_range: str) -> tuple[int, int] | None:
    match = _PORT_RANGE_RE.match((port_range or "").strip())
    if not match:
        return None
    start, end = int(match.group(1)), int(match.group(2))
    if not (1 <= start <= end <= 65535):
        return None
    return start, end


def _reader_loop(process: subprocess.Popen, scan_id: int) -> None:
    stdout, stderr = process.communicate()
    with _lock:
        if _state["scan_id"] != scan_id:
            return  # superseded by a newer scan
        _state["running"] = False

        if _state["_stopped"]:
            _state["ok"] = False
            _state["message"] = "stopped"
            return

        if process.returncode != 0:
            _state["ok"] = False
            _state["message"] = (stderr or stdout or "nmap failed").strip() or "nmap failed"
            return

        ports = []
        for line in stdout.splitlines():
            match = _PORT_LINE_RE.match(line.strip())
            if match:
                ports.append(
                    {
                        "port": int(match.group(1)),
                        "protocol": match.group(2),
                        "state": match.group(3),
                        "service": match.group(4),
                    }
                )
        _state["ports"] = ports
        _state["ok"] = True
        _state["message"] = None


def start(host: str, port_range: str, interface: str = "eth0") -> dict:
    host = (host or "").strip()
    if not host:
        return {"ok": False, "message": "host is required"}
    if host.startswith("-"):
        return {"ok": False, "message": "invalid host"}

    parsed_range = _parse_port_range(port_range)
    if parsed_range is None:
        return {
            "ok": False,
            "message": "port range must be START-END within 1-65535, e.g. 1-1024",
        }
    start_port, end_port = parsed_range

    nmap_bin = shell.find_binary(_NMAP_CANDIDATES)
    if not nmap_bin:
        return {"ok": False, "message": "nmap not available"}
    sudo_bin = shell.find_binary(_SUDO_CANDIDATES)
    if not sudo_bin:
        return {"ok": False, "message": "sudo not available"}

    source_ip = _eth0_source_ip()
    if not source_ip:
        return {
            "ok": False,
            "message": "eth0 has no IP address -- switch to DHCP or Static mode first "
                       "(Passive mode has no source address to scan from)",
        }

    args = [
        sudo_bin, nmap_bin, "-sS", "-Pn", "-n", "-e", interface,
        "-p", f"{start_port}-{end_port}", "--open", host,
    ]

    with _lock:
        old_process = _state.get("process")
        if old_process is not None and _state["running"]:
            _terminate(old_process)

        scan_id = _state["scan_id"] + 1

        try:
            process = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True,
            )
        except OSError as exc:
            return {"ok": False, "message": str(exc)}

        _state.update(
            {
                "running": True,
                "host": host,
                "port_range": f"{start_port}-{end_port}",
                "scan_id": scan_id,
                "process": process,
                "ok": None,
                "message": None,
                "ports": [],
                "_stopped": False,
            }
        )

    threading.Thread(target=_reader_loop, args=(process, scan_id), daemon=True).start()
    return {"ok": True}


def status() -> dict:
    with _lock:
        return {
            "running": _state["running"],
            "host": _state["host"],
            "port_range": _state["port_range"],
            "ok": _state["ok"],
            "message": _state["message"],
            "ports": _state["ports"],
        }


def stop() -> dict:
    with _lock:
        process = _state.get("process")
        if process is not None and _state["running"]:
            _state["_stopped"] = True
            _terminate(process)
            return {"ok": True, "message": "scan stopping"}
        return {"ok": True, "message": "no scan running"}
