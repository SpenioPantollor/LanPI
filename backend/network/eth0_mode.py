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

import subprocess

from backend import shell

CONNECTION_NAME = "lanpi-eth0"
_INTERFACE = "eth0"
_NMCLI_CANDIDATES = ["/usr/bin/nmcli", "/bin/nmcli", "nmcli"]

# NetworkManager's own defaults are ethernet=100, wifi=600 -- eth0
# would outrank wlan0's default route by default, exactly backwards
# from what Rule 3 needs. A comfortably-higher fixed value (lower
# priority) keeps eth0's default route usable (needed for real
# industrial gear like Siemens S7 PLCs that live behind their own
# gateway, not just devices on eth0's own subnet) while still leaving
# wlan0 preferred whenever it actually has a route -- eth0 only ends
# up "the" default when wlan0 has none at all (e.g. mid-fallback-AP).
_ETH0_ROUTE_METRIC = "700"


def _find_nmcli() -> str | None:
    return shell.find_binary(_NMCLI_CANDIDATES)


def _run(args: list[str]) -> subprocess.CompletedProcess | None:
    nmcli = _find_nmcli()
    if not nmcli:
        return None
    return shell.run([nmcli, *args], timeout=20)


def _run_privileged(args: list[str]) -> subprocess.CompletedProcess | None:
    """State-changing nmcli calls (disconnect/up/modify/add) need
    NetworkManager's network-control polkit permission, which the
    unprivileged service user doesn't get without an active login
    session -- go through sudo instead (passwordless for this user)."""
    nmcli = _find_nmcli()
    if not nmcli:
        return None
    return shell.run_privileged([nmcli, *args], timeout=20)


def _ensure_profile() -> bool:
    """Create the lanpi-eth0 connection profile if it doesn't exist yet.
    Never auto-activated (autoconnect=no) -- passive is the default.

    eth0 is allowed a real default route via its own gateway when
    connected (maintainer's call, 2026-08-22, superseding the
    ipv4.never-default=yes / secondary-routing-table approach tried
    first): plenty of real industrial gear (Siemens S7 PLCs among
    others) lives behind its own gateway on the test network, not just
    on eth0's own directly-connected subnet, and a from-address policy
    route only ever helped the handful of tools that explicitly bind
    to eth0's address (ping/mtr/tcp_test) -- mtr's own `-a` handling
    turned out not to work with it anyway (see STATUS.md). A plain
    route-metric override is simpler and covers everything uniformly.
    ipv4.route-metric keeps wlan0 preferred by default (see
    _ETH0_ROUTE_METRIC above) rather than eth0 unconditionally winning
    -- less strict than never-default, but the metric ordering only
    breaks down if wlan0's own metric is ever pushed above this value,
    which nothing in this codebase does.
    """
    result = _run(["-t", "-f", "NAME", "connection", "show"])
    if result is None:
        return False
    names = result.stdout.strip().splitlines()
    if CONNECTION_NAME in names:
        _run_privileged(["connection", "modify", CONNECTION_NAME,
                          "ipv4.never-default", "no", "ipv4.route-metric", _ETH0_ROUTE_METRIC])
        return True

    create = _run_privileged(
        ["connection", "add", "type", "ethernet", "ifname", _INTERFACE,
         "con-name", CONNECTION_NAME, "autoconnect", "no",
         "ipv4.method", "auto", "ipv4.route-metric", _ETH0_ROUTE_METRIC]
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
    # nmcli dumps every DHCP4.OPTION[n] as "key = value" -- keep them
    # all rather than picking out just "routers" as before, so lease
    # details (server, lease time, domain) can be surfaced too.
    dhcp_options: dict[str, str] = {}
    for line in result.stdout.strip().splitlines():
        if line.startswith("GENERAL.CONNECTION:"):
            connection = line.split(":", 1)[1]
        elif line.startswith("IP4.ADDRESS"):
            address = line.split(":", 1)[1]
        elif line.startswith("IP4.GATEWAY:"):
            gateway = line.split(":", 1)[1]
        elif line.startswith("IP4.DNS"):
            dns.append(line.split(":", 1)[1])
        elif line.startswith("DHCP4.OPTION"):
            _, _, rest = line.partition(":")
            key, sep, value = rest.strip().partition(" = ")
            if sep:
                dhcp_options[key.strip()] = value.strip()

    if not connection or connection == "--":
        return {
            "available": True, "mode": "passive",
            "address": None, "gateway": None, "dns": [],
            "lease_time_seconds": None, "dhcp_server": None, "domain_name": None,
        }

    method_result = _run(["-t", "-f", "ipv4.method", "connection", "show", connection])
    method = "auto"
    if method_result and method_result.returncode == 0:
        method = method_result.stdout.strip().split(":", 1)[-1].strip() or "auto"

    mode = "static" if method == "manual" else "dhcp"

    # IP4.GATEWAY is normally populated directly now that eth0 gets a
    # real (if deprioritized) default route -- this fallback only
    # matters for the brief window right after a mode switch, before
    # NetworkManager finishes applying it.
    if not gateway:
        if mode == "dhcp":
            gateway = dhcp_options.get("routers")
        else:
            gw_result = _run(["-t", "-f", "ipv4.gateway", "connection", "show", connection])
            if gw_result and gw_result.returncode == 0:
                gateway = gw_result.stdout.strip().split(":", 1)[-1].strip() or None

    lease_time = None
    dhcp_server = None
    domain_name = None
    if mode == "dhcp":
        raw_lease = dhcp_options.get("dhcp_lease_time")
        if raw_lease and raw_lease.isdigit():
            lease_time = int(raw_lease)
        dhcp_server = dhcp_options.get("dhcp_server_identifier") or None
        domain_name = dhcp_options.get("domain_name") or None

    return {
        "available": True,
        "mode": mode,
        "address": address,
        "gateway": gateway,
        "dns": dns,
        "lease_time_seconds": lease_time,
        "dhcp_server": dhcp_server,
        "domain_name": domain_name,
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
