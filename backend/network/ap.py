"""Fallback access point configuration (hostapd.conf) management.

The AP itself is brought up/down by system/lanpi-ap-up.sh and
system/lanpi-ap-down.sh (hostapd + dnsmasq + an nftables port-80
redirect) -- see activate()/deactivate() below, which run those
scripts. This module also reads/edits the hostapd config and, if the
AP happens to be active, restarts hostapd so a change takes effect
immediately.
"""

from __future__ import annotations

import os
import re
import tempfile

from backend import shell

HOSTAPD_CONF_PATH = "/etc/hostapd/hostapd.conf"
AP_ADDRESS = "172.24.58.1"

_REPO_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_AP_UP_SCRIPT = os.path.join(_REPO_DIR, "system", "lanpi-ap-up.sh")
_AP_DOWN_SCRIPT = os.path.join(_REPO_DIR, "system", "lanpi-ap-down.sh")

_SYSTEMCTL_CANDIDATES = ["/usr/bin/systemctl", "/bin/systemctl", "systemctl"]


def activate() -> dict:
    """Bring the fallback AP up (system/lanpi-ap-up.sh)."""
    result = shell.run_privileged([_AP_UP_SCRIPT], timeout=20)
    if result is None:
        return {"ok": False, "message": "sudo not available or command timed out"}
    return {"ok": result.returncode == 0, "message": (result.stdout or result.stderr).strip()}


def deactivate() -> dict:
    """Tear the fallback AP down and hand wlan0 back to NetworkManager
    (system/lanpi-ap-down.sh)."""
    result = shell.run_privileged([_AP_DOWN_SCRIPT], timeout=20)
    if result is None:
        return {"ok": False, "message": "sudo not available or command timed out"}
    return {"ok": result.returncode == 0, "message": (result.stdout or result.stderr).strip()}


def is_active() -> bool:
    systemctl = shell.find_binary(_SYSTEMCTL_CANDIDATES)
    if not systemctl:
        return False
    result = shell.run([systemctl, "is-active", "hostapd"], timeout=5)
    return result is not None and result.stdout.strip() == "active"


def get_ssid() -> str | None:
    try:
        with open(HOSTAPD_CONF_PATH) as f:
            content = f.read()
    except OSError:
        return None
    match = re.search(r"^ssid=(.*)$", content, re.MULTILINE)
    return match.group(1).strip() if match else None


def set_config(ssid: str, password: str | None) -> dict:
    """Update the fallback AP's SSID and/or password. Password is left
    unchanged if omitted/empty. Restarts hostapd if currently active."""
    ssid = ssid.strip()
    if not ssid or len(ssid) > 32:
        return {"ok": False, "message": "SSID must be 1-32 characters"}
    if password and len(password) < 8:
        return {"ok": False, "message": "password must be at least 8 characters"}

    try:
        with open(HOSTAPD_CONF_PATH) as f:
            lines = f.readlines()
    except OSError as exc:
        return {"ok": False, "message": f"could not read config: {exc}"}

    new_lines = []
    for line in lines:
        if line.startswith("ssid="):
            new_lines.append(f"ssid={ssid}\n")
        elif line.startswith("wpa_passphrase=") and password:
            new_lines.append(f"wpa_passphrase={password}\n")
        else:
            new_lines.append(line)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
            tmp.writelines(new_lines)
            tmp_path = tmp.name
        result = shell.run_privileged(["cp", tmp_path, HOSTAPD_CONF_PATH], timeout=5)
    finally:
        if tmp_path:
            os.unlink(tmp_path)

    if result is None:
        return {"ok": False, "message": "sudo not available or command timed out"}
    if result.returncode != 0:
        return {"ok": False, "message": result.stderr.strip()}

    if is_active():
        systemctl = shell.find_binary(_SYSTEMCTL_CANDIDATES)
        if systemctl:
            shell.run_privileged([systemctl, "restart", "hostapd"], timeout=10)

    return {"ok": True, "message": "Fallback AP configuration updated"}
