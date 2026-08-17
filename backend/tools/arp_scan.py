"""Active ARP scan of the TEST PORT's local network, via arp-scan.

Works even when eth0 has no configured IP (Passive mode) if a network
is given explicitly; `--localnet` (the default) needs eth0 to have an
address already (DHCP/Static mode) to know what to scan.
"""

from __future__ import annotations

import re
import shutil
import subprocess

_ARP_SCAN_CANDIDATES = ["/usr/bin/arp-scan", "/usr/sbin/arp-scan", "arp-scan"]
_HOST_RE = re.compile(r"^(\d+\.\d+\.\d+\.\d+)\t([0-9a-fA-F:]+)\t?(.*)$")


def _find_arp_scan() -> str | None:
    for candidate in _ARP_SCAN_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def scan(interface: str = "eth0", network: str | None = None) -> dict:
    arp_scan_bin = _find_arp_scan()
    if not arp_scan_bin:
        return {"ok": False, "message": "arp-scan not available", "hosts": []}

    args = [arp_scan_bin, "--interface", interface, "--retry=1", "--timeout=500"]
    args.append(network.strip() if network else "--localnet")

    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "message": str(exc), "hosts": []}

    if result.returncode != 0:
        return {"ok": False, "message": (result.stderr or result.stdout).strip(), "hosts": []}

    hosts = []
    for line in result.stdout.splitlines():
        match = _HOST_RE.match(line)
        if match:
            hosts.append(
                {
                    "ip": match.group(1),
                    "mac": match.group(2).lower(),
                    "vendor": match.group(3).strip() or None,
                }
            )

    return {"ok": True, "message": None, "hosts": hosts}
