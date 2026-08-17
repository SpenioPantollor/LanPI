"""IP host-discovery scan on the TEST PORT (eth0), via nmap -sn.

Ping-sweep host discovery (nmap combines ICMP, ARP, and TCP probes
under -sn), complementary to arp_scan.py's pure-ARP approach: takes an
explicit CIDR/range and needs eth0 to have an address to scan from
(arp_scan can work from Passive mode with an explicit network since
ARP doesn't need a source address).

Runs as a background process (mirrors mtr.py/ping.py), with results
streamed live as nmap reports each host -- not just at the end -- and
stoppable mid-scan. Learned from the MTR feedback: a scan against a
mostly-unresponsive range can take a while, and users want to cancel
it rather than wait it out. Whole process group killed on stop (same
mtr-packet-orphan lesson: don't assume signalling just the tracked
process is enough).
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import threading

from backend.network import eth0_mode

_NMAP_CANDIDATES = ["/usr/bin/nmap", "/usr/sbin/nmap", "nmap"]
_REPORT_RE = re.compile(r"^Nmap scan report for (\S+)")
_MAC_RE = re.compile(r"^MAC Address: ([0-9A-Fa-f:]{17})(?:\s+\((.+)\))?")

_lock = threading.Lock()
_state = {
    "running": False,
    "target": None,
    "scan_id": 0,
    "process": None,
    "hosts": [],
    "message": None,
}


def _find_nmap() -> str | None:
    for candidate in _NMAP_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _eth0_source_ip() -> str | None:
    mode = eth0_mode.get_mode()
    address = mode.get("address")
    return address.split("/")[0] if address else None


def _terminate(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def _reader_loop(process: subprocess.Popen, scan_id: int) -> None:
    hosts: list[dict] = []
    for line in process.stdout:
        with _lock:
            if _state["scan_id"] != scan_id:
                return  # superseded by a newer scan

        report_match = _REPORT_RE.match(line)
        if report_match:
            hosts.append({"ip": report_match.group(1), "mac": None, "vendor": None})
            with _lock:
                if _state["scan_id"] == scan_id:
                    _state["hosts"] = list(hosts)
            continue

        mac_match = _MAC_RE.match(line)
        if mac_match and hosts:
            hosts[-1]["mac"] = mac_match.group(1).lower()
            hosts[-1]["vendor"] = mac_match.group(2)
            with _lock:
                if _state["scan_id"] == scan_id:
                    _state["hosts"] = list(hosts)

    process.wait()
    with _lock:
        if _state["scan_id"] == scan_id:
            _state["running"] = False


def start(target: str, interface: str = "eth0") -> dict:
    target = (target or "").strip()
    if not target:
        return {"ok": False, "message": "target network/range is required"}
    if target.startswith("-"):
        return {"ok": False, "message": "invalid target"}

    nmap_bin = _find_nmap()
    if not nmap_bin:
        return {"ok": False, "message": "nmap not available"}

    source_ip = _eth0_source_ip()
    if not source_ip:
        return {
            "ok": False,
            "message": "eth0 has no IP address -- switch to DHCP or Static mode first "
                       "(Passive mode has no source address to scan from)",
        }

    args = [nmap_bin, "-sn", "-n", "-e", interface, target]

    with _lock:
        old_process = _state.get("process")
        if old_process is not None and _state["running"]:
            _terminate(old_process)

        scan_id = _state["scan_id"] + 1

        try:
            process = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                bufsize=1, start_new_session=True,
            )
        except OSError as exc:
            return {"ok": False, "message": str(exc)}

        _state.update(
            {
                "running": True,
                "target": target,
                "scan_id": scan_id,
                "process": process,
                "hosts": [],
                "message": None,
            }
        )

    threading.Thread(target=_reader_loop, args=(process, scan_id), daemon=True).start()
    return {"ok": True}


def status() -> dict:
    with _lock:
        return {
            "running": _state["running"],
            "target": _state["target"],
            "hosts": _state["hosts"],
        }


def stop() -> dict:
    with _lock:
        process = _state.get("process")
        if process is not None and _state["running"]:
            _terminate(process)
            return {"ok": True, "message": "scan stopping"}
        return {"ok": True, "message": "no scan running"}
