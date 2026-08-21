"""Wi-Fi management (client connections + fallback AP status) via nmcli.

wlan0 is the management interface. This module only ever touches
wlan0 / NetworkManager Wi-Fi connections -- never eth0.
"""

from __future__ import annotations

import subprocess
import time

from backend import shell
from backend.network import ap

_INTERFACE = "wlan0"
_NMCLI_CANDIDATES = ["/usr/bin/nmcli", "/bin/nmcli", "nmcli"]


def _find_nmcli() -> str | None:
    return shell.find_binary(_NMCLI_CANDIDATES)


def _run(args: list[str]) -> subprocess.CompletedProcess | None:
    nmcli = _find_nmcli()
    if not nmcli:
        return None
    return shell.run([nmcli, *args], timeout=20)


def _run_privileged(args: list[str]) -> subprocess.CompletedProcess | None:
    """State-changing nmcli calls (connect/delete) need NetworkManager's
    network-control polkit permission, which the unprivileged service
    user doesn't get without an active login session -- go through
    sudo instead (passwordless for this user)."""
    nmcli = _find_nmcli()
    if not nmcli:
        return None
    return shell.run_privileged([nmcli, *args], timeout=30)


def _parse_terse(output: str, fields: list[str]) -> list[dict]:
    rows = []
    for line in output.strip().splitlines():
        if not line:
            continue
        parts = line.split(":")
        rows.append(dict(zip(fields, parts)))
    return rows


def _get_connection_ssid(connection_name: str) -> str:
    """Resolve a connection profile's actual SSID (profile name and SSID
    often differ, e.g. NetworkManager profiles imported from netplan)."""
    result = _run(["-t", "-f", "802-11-wireless.ssid", "connection", "show", connection_name])
    if result is None or result.returncode != 0:
        return connection_name
    ssid = result.stdout.strip().split(":", 1)[-1].strip()
    return ssid or connection_name


def get_status() -> dict:
    """Current wlan0 state: client-connected, AP-active, or disconnected.

    The fallback AP runs via hostapd, outside NetworkManager's control
    (wlan0 is set unmanaged while it's active) -- so AP mode is detected
    by asking hostapd directly, not via nmcli.
    """
    if ap.is_active():
        return {
            "available": True,
            "connected": True,
            "mode": "ap",
            "ssid": ap.get_ssid(),
            "ip_address": ap.AP_ADDRESS,
        }

    result = _run(
        ["-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS",
         "device", "show", _INTERFACE]
    )
    if result is None or result.returncode != 0:
        return {"available": False}

    state = None
    connection = None
    ip_address = None
    for line in result.stdout.strip().splitlines():
        if line.startswith("GENERAL.STATE:"):
            state = line.split(":", 1)[1]
        elif line.startswith("GENERAL.CONNECTION:"):
            connection = line.split(":", 1)[1]
        elif line.startswith("IP4.ADDRESS"):
            ip_address = line.split(":", 1)[1]

    connected = bool(state and state.startswith("100"))
    mode = "client" if connected else "none"

    ssid = None
    if connected and connection:
        ssid = _get_connection_ssid(connection)

    return {
        "available": True,
        "connected": connected,
        "mode": mode,
        "ssid": ssid,
        "ip_address": ip_address.split("/")[0] if ip_address else None,
    }


def scan() -> list[dict]:
    """Rescan and list nearby Wi-Fi networks.

    --rescan yes needs the same network-control polkit permission as
    other state-changing nmcli calls -- without privilege it silently
    returns only the cached/currently-associated network instead of a
    fresh scan (confirmed live: 1 network unprivileged vs. 7 real
    nearby networks with sudo).

    Returns nothing useful while the fallback AP is active: wlan0 is
    owned by hostapd then, not NetworkManager, and this Pi has a
    single Wi-Fi radio so it can't scan and run the AP at once. Use
    add_known() instead in that situation -- see its docstring.
    """
    fields = ["SSID", "SIGNAL", "SECURITY"]
    result = _run_privileged(
        ["-t", "-f", ",".join(fields), "device", "wifi", "list",
         "ifname", _INTERFACE, "--rescan", "yes"]
    )
    if result is None or result.returncode != 0:
        return []

    networks = []
    seen_ssids = set()
    for row in _parse_terse(result.stdout, fields):
        ssid = row.get("SSID", "").strip()
        if not ssid or ssid in seen_ssids:
            continue
        seen_ssids.add(ssid)
        signal = row.get("SIGNAL", "")
        networks.append(
            {
                "ssid": ssid,
                "signal": int(signal) if signal.isdigit() else None,
                "secured": bool(row.get("SECURITY")),
            }
        )
    networks.sort(key=lambda n: n["signal"] or 0, reverse=True)
    return networks


def list_saved() -> list[dict]:
    """Wi-Fi client connection profiles saved on this device.

    `name` is the NetworkManager connection profile name (the stable
    identifier to use with `forget`); `ssid` is the actual network name,
    which can differ from `name` (e.g. profiles imported from netplan).
    """
    fields = ["NAME", "TYPE"]
    result = _run(["-t", "-f", ",".join(fields), "connection", "show"])
    if result is None or result.returncode != 0:
        return []

    saved = []
    for row in _parse_terse(result.stdout, fields):
        if row.get("TYPE") != "802-11-wireless":
            continue
        name = row.get("NAME", "")
        saved.append({"name": name, "ssid": _get_connection_ssid(name)})
    return saved


def connect(ssid: str, password: str | None = None) -> dict:
    """Connect to a Wi-Fi network, creating or reusing a saved profile."""
    args = ["device", "wifi", "connect", ssid, "ifname", _INTERFACE]
    if password:
        args += ["password", password]
    result = _run_privileged(args)
    if result is None:
        return {"ok": False, "message": "nmcli/sudo not available"}
    return {
        "ok": result.returncode == 0,
        "message": (result.stdout or result.stderr).strip(),
    }


def add_known(ssid: str, password: str | None = None) -> dict:
    """Save a Wi-Fi profile and, if the fallback AP currently owns
    wlan0, immediately try switching over to it.

    Solves the "closed loop" case: reachable only via the fallback AP,
    but scan()/connect() need wlan0 in station mode, which the AP is
    occupying (single Wi-Fi radio -- AP and station mode can't run at
    once). Writing the profile itself never touches the live device,
    so that part always works regardless of AP state. If the AP was
    active, this then tears it down and tries to bring the new
    connection up right away -- that's the whole point of using this
    from the fallback AP -- but restores the AP automatically if the
    new network doesn't come up, so a wrong password doesn't strand
    the device with no way back to the dashboard.
    """
    ssid = ssid.strip()
    if not ssid:
        return {"ok": False, "message": "SSID is required"}
    args = [
        "connection", "add", "type", "wifi",
        "con-name", ssid, "ifname", _INTERFACE, "ssid", ssid,
        "autoconnect", "yes",
    ]
    if password:
        args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password]
    result = _run_privileged(args)
    if result is None:
        return {"ok": False, "message": "nmcli/sudo not available"}
    if result.returncode != 0:
        return {"ok": False, "message": (result.stdout or result.stderr).strip()}

    message = (result.stdout or result.stderr).strip()

    was_ap_active = ap.is_active()
    if not was_ap_active:
        return {"ok": True, "message": message}

    # The whole point of adding a network from here is to get onto it --
    # try immediately. Single Wi-Fi radio means the AP has to come down
    # first; if the new network doesn't actually come up, restore the AP
    # so the device isn't left with no way to reach it at all.
    down = ap.deactivate()
    if not down.get("ok"):
        return {
            "ok": True,
            "message": f"{message} (saved, but could not leave fallback AP mode to connect: {down.get('message')})",
        }

    time.sleep(2)
    connect_result = _run_privileged(["connection", "up", ssid])
    connected = connect_result is not None and connect_result.returncode == 0

    if not connected:
        ap.activate()
        return {
            "ok": True,
            "message": f"{message} (saved, but couldn't connect right now -- fallback AP restored)",
        }

    return {"ok": True, "message": f"{message}, connected"}


def forget(name: str) -> dict:
    """Delete a saved Wi-Fi connection profile, by its connection name
    (see list_saved -- this is `name`, not necessarily the SSID)."""
    result = _run_privileged(["connection", "delete", "id", name])
    if result is None:
        return {"ok": False, "message": "nmcli/sudo not available"}
    return {
        "ok": result.returncode == 0,
        "message": (result.stdout or result.stderr).strip(),
    }
