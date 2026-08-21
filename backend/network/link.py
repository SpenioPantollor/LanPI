"""Ethernet link status for the TEST PORT (eth0), via `ip` and `ethtool`."""

from __future__ import annotations

import json
import re

from backend import shell

_IP_CANDIDATES = ["/usr/bin/ip", "/bin/ip", "ip"]
_ETHTOOL_CANDIDATES = ["/sbin/ethtool", "/usr/sbin/ethtool", "ethtool"]

_SPEED_RE = re.compile(r"Speed:\s*(\d+)Mb/s")
_DUPLEX_RE = re.compile(r"Duplex:\s*(Full|Half)", re.IGNORECASE)
_AUTONEG_RE = re.compile(r"Auto-negotiation:\s*(on|off)", re.IGNORECASE)
_LINK_DETECTED_RE = re.compile(r"Link detected:\s*(yes|no)", re.IGNORECASE)


def _run(cmd: list[str]) -> str:
    result = shell.run(cmd, timeout=5)
    return result.stdout if result else ""


def _get_ip_link_info(interface: str) -> dict:
    ip_bin = shell.find_binary(_IP_CANDIDATES)
    if not ip_bin:
        return {}
    output = _run([ip_bin, "-j", "-s", "link", "show", interface])
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return {}
    return data[0] if data else {}


def _get_ethtool_info(interface: str) -> dict:
    info = {"speed_mbps": None, "duplex": None, "autoneg": None, "link_detected": None}
    ethtool_bin = shell.find_binary(_ETHTOOL_CANDIDATES)
    if not ethtool_bin:
        return info

    output = _run([ethtool_bin, interface])

    speed_match = _SPEED_RE.search(output)
    if speed_match:
        info["speed_mbps"] = int(speed_match.group(1))

    duplex_match = _DUPLEX_RE.search(output)
    if duplex_match:
        info["duplex"] = duplex_match.group(1).lower()

    autoneg_match = _AUTONEG_RE.search(output)
    if autoneg_match:
        info["autoneg"] = autoneg_match.group(1).lower() == "on"

    link_match = _LINK_DETECTED_RE.search(output)
    if link_match:
        info["link_detected"] = link_match.group(1).lower() == "yes"

    return info


def get_link_status(interface: str = "eth0") -> dict:
    link_info = _get_ip_link_info(interface)
    ethtool_info = _get_ethtool_info(interface)

    stats = link_info.get("stats64", {})
    rx = stats.get("rx", {})
    tx = stats.get("tx", {})

    return {
        "interface": interface,
        "present": bool(link_info),
        "operstate": link_info.get("operstate"),
        "mac_address": link_info.get("address"),
        "mtu": link_info.get("mtu"),
        "speed_mbps": ethtool_info["speed_mbps"],
        "duplex": ethtool_info["duplex"],
        "autoneg": ethtool_info["autoneg"],
        "link_detected": ethtool_info["link_detected"],
        "rx_bytes": rx.get("bytes"),
        "rx_packets": rx.get("packets"),
        "rx_errors": rx.get("errors"),
        "rx_dropped": rx.get("dropped"),
        "tx_bytes": tx.get("bytes"),
        "tx_packets": tx.get("packets"),
        "tx_errors": tx.get("errors"),
        "tx_dropped": tx.get("dropped"),
    }
