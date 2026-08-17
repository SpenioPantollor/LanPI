"""TEST PORT (eth0) IP mode: Passive / DHCP / Static, via nmcli.

Passive is the implicit default (ARCHITECTURE.MD Rule 4): eth0 simply
has no active NetworkManager connection, so it carries no IPv4/IPv6
address and generates no Layer 3 traffic, while still allowing passive
functions (link monitoring, LLDP, packet capture) that talk to the
interface directly. install.sh disables autoconnect on every
ethernet profile bound to eth0 (including any pre-existing
OS-generated one) so this holds automatically at every boot -- no
extra boot-time service needed here, unlike the wlan0 fallback AP.
"""

from __future__ import annotations

import shutil
import subprocess

CONNECTION_NAME = "lanpi-eth0"
_INTERFACE = "eth0"
_NMCLI_CANDIDATES = ["/usr/bin/nmcli", "/bin/nmcli", "nmcli"]
_SUDO_CANDIDATES = ["/usr/bin/sudo", "/bin/sudo", "sudo"]


def _find_binary(candidates: list[str]) -> str | None:
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _find_nmcli() -> str | None:
    return _find_binary(_NMCLI_CANDIDATES)


def _run(args: list[str]) -> subprocess.CompletedProcess | None:
    nmcli = _find_nmcli()
    if not nmcli:
        return None
    try:
        return subprocess.run(
            [nmcli, *args], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _run_privileged(args: list[str]) -> subprocess.CompletedProcess | None:
    """State-changing nmcli calls (disconnect/up/modify/add) need
    NetworkManager's network-control polkit permission, which the
    unprivileged service user doesn't get without an active login
    session -- go through sudo instead (passwordless for this user)."""
    nmcli = _find_nmcli()
    sudo = _find_binary(_SUDO_CANDIDATES)
    if not nmcli or not sudo:
        return None
    try:
        return subprocess.run(
            [sudo, nmcli, *args], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _ensure_profile() -> bool:
    """Create the lanpi-eth0 connection profile if it doesn't exist yet.
    Never auto-activated (autoconnect=no) -- passive is the default.

    ipv4.never-default is critical: eth0 is the TEST PORT and must
    never contribute a default route, even when it has DHCP/static
    gateway info -- otherwise a test network's gateway can outrank
    wlan0's and silently steal all of the Pi's own outbound traffic
    (including its own management/update path). Confirmed live: a
    leftover eth0 static route with a lower metric than wlan0's did
    exactly this and cut off internet/git access until manually fixed.
    """
    result = _run(["-t", "-f", "NAME", "connection", "show"])
    if result is None:
        return False
    names = result.stdout.strip().splitlines()
    if CONNECTION_NAME in names:
        _run_privileged(["connection", "modify", CONNECTION_NAME, "ipv4.never-default", "yes"])
        return True

    create = _run_privileged(
        ["connection", "add", "type", "ethernet", "ifname", _INTERFACE,
         "con-name", CONNECTION_NAME, "autoconnect", "no",
         "ipv4.method", "auto", "ipv4.never-default", "yes"]
    )
    return create is not None and create.returncode == 0


def get_mode() -> dict:
    result = _run(
        ["-t", "-f", "GENERAL.CONNECTION,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS,DHCP4.OPTION",
         "device", "show", _INTERFACE]
    )
    if result is None or result.returncode != 0:
        return {"available": False}

    connection = None
    address = None
    gateway = None
    dns = []
    dhcp_router = None
    for line in result.stdout.strip().splitlines():
        if line.startswith("GENERAL.CONNECTION:"):
            connection = line.split(":", 1)[1]
        elif line.startswith("IP4.ADDRESS"):
            address = line.split(":", 1)[1]
        elif line.startswith("IP4.GATEWAY:"):
            gateway = line.split(":", 1)[1]
        elif line.startswith("IP4.DNS"):
            dns.append(line.split(":", 1)[1])
        elif line.startswith("DHCP4.OPTION") and "routers = " in line:
            dhcp_router = line.split("routers = ", 1)[1].strip()

    if not connection or connection == "--":
        return {
            "available": True, "mode": "passive",
            "address": None, "gateway": None, "dns": [],
        }

    method_result = _run(["-t", "-f", "ipv4.method", "connection", "show", connection])
    method = "auto"
    if method_result and method_result.returncode == 0:
        method = method_result.stdout.strip().split(":", 1)[-1].strip() or "auto"

    mode = "static" if method == "manual" else "dhcp"

    # ipv4.never-default (Rule 3 -- eth0 must never be the default
    # route) means NetworkManager doesn't install a gateway route, so
    # IP4.GATEWAY comes back empty even though DHCP/static config
    # still has one. Fall back to showing it for information, without
    # ever actually installing it as a route.
    if not gateway:
        if mode == "dhcp":
            gateway = dhcp_router
        else:
            gw_result = _run(["-t", "-f", "ipv4.gateway", "connection", "show", connection])
            if gw_result and gw_result.returncode == 0:
                gateway = gw_result.stdout.strip().split(":", 1)[-1].strip() or None

    return {
        "available": True,
        "mode": mode,
        "address": address,
        "gateway": gateway,
        "dns": dns,
    }


def set_passive() -> dict:
    result = _run_privileged(["device", "disconnect", _INTERFACE])
    if result is None:
        return {"ok": False, "message": "nmcli/sudo not available"}
    # "disconnect" errors if eth0 has no active connection already --
    # that's already the desired state, not a failure.
    if result.returncode != 0 and "not active" not in (result.stderr or ""):
        return {"ok": False, "message": result.stderr.strip()}
    return {"ok": True, "message": "eth0 set to passive mode"}


def set_dhcp() -> dict:
    if not _ensure_profile():
        return {"ok": False, "message": "could not create lanpi-eth0 profile"}
    # Explicitly clear any leftover static settings from a previous
    # Static-mode call -- ipv4.method alone isn't enough: NetworkManager
    # keeps applying a manually configured ipv4.addresses/gateway
    # alongside the DHCP lease if they're left set on the profile.
    _run_privileged(
        ["connection", "modify", CONNECTION_NAME,
         "ipv4.method", "auto",
         "ipv4.addresses", "",
         "ipv4.gateway", "",
         "ipv4.dns", ""]
    )
    result = _run_privileged(["connection", "up", CONNECTION_NAME])
    if result is None or result.returncode != 0:
        message = result.stderr.strip() if result else "nmcli/sudo not available"
        return {"ok": False, "message": message}
    return {"ok": True, "message": "eth0 set to DHCP mode"}


def set_static(address: str, gateway: str = "", dns: str = "") -> dict:
    """address must be CIDR form, e.g. 192.168.20.200/24."""
    if not address or "/" not in address:
        return {"ok": False, "message": "address must be in CIDR form, e.g. 192.168.20.200/24"}
    if not _ensure_profile():
        return {"ok": False, "message": "could not create lanpi-eth0 profile"}

    args = [
        "connection", "modify", CONNECTION_NAME,
        "ipv4.method", "manual",
        "ipv4.addresses", address,
    ]
    args += ["ipv4.gateway", gateway if gateway else ""]
    args += ["ipv4.dns", dns if dns else ""]
    _run_privileged(args)

    result = _run_privileged(["connection", "up", CONNECTION_NAME])
    if result is None or result.returncode != 0:
        message = result.stderr.strip() if result else "nmcli/sudo not available"
        return {"ok": False, "message": message}
    return {"ok": True, "message": "eth0 set to static mode"}
