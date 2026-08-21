"""Shared helpers for shelling out to system binaries: consistent
binary discovery and subprocess.run wrapping (v0.2.3 Foundation #10),
factored out of what was previously ~12 near-identical copies of the
same shutil.which loop and try/except OSError/TimeoutExpired pattern
scattered across backend/network/*.py, backend/tools/*.py, and
backend/capture/*.py.

Deliberately does NOT wrap Popen-based background process spawning --
mtr.py, ping.py, pcap.py, dispatcher.py, ip_scanner.py, and
port_scanner.py all still build their own Popen calls directly, since
returning immediately (not blocking) is a different concern from this
module's one-shot blocking commands; folding both into one helper
would blur that distinction rather than clarify it. Those modules
still use find_binary() below for binary discovery, just not run().
"""

from __future__ import annotations

import shutil
import subprocess

_SUDO_CANDIDATES = ["/usr/bin/sudo", "/bin/sudo", "sudo"]


def find_binary(candidates: list[str]) -> str | None:
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def run(args: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def run_privileged(args: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess | None:
    """Prefixes args with sudo (passwordless for the service user, see
    system/install.sh) -- for state-changing calls that need real
    privilege NetworkManager's polkit won't grant an unprivileged
    service user without an active login session (nmcli connection
    changes, hostapd config, and similar)."""
    sudo = find_binary(_SUDO_CANDIDATES)
    if not sudo:
        return None
    return run([sudo, *args], timeout=timeout)
