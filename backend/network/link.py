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
_PHY_STAT_LINE_RE = re.compile(r"^\s*(\w+):\s*(-?\d+)\s*$")

# PHY-level link-training/signal-quality counters (ethtool --phy-statistics),
# not a full cable test -- Linux's Broadcom PHY driver never wires up
# cable_test_start/cable_test_get_status for the BCM54213PE used on the Pi
# 4 (a community attempt to patch it in hung indefinitely on a disconnected
# cable, never upstreamed -- see README's Cable Diagnostics section), but
# these counters are a real, working, no-patch-needed proxy: local_rcvr_nok/
# remote_rcv_nok come from 1000BASE-T's own link-training handshake and rise
# on a marginal/degrading cable *before* the link actually drops. Only
# whichever keys the driver actually reports are included -- coverage
# varies by NIC/PHY driver (confirmed present on the Pi 4's bcmgenet/
# BCM54213PE; not checked against the Pi 3's USB smsc95xx adapter).
_PHY_STAT_KEYS = {
    "phy_receive_errors",
    "phy_serdes_ber_errors",
    "phy_false_carrier_sense_errors",
    "phy_local_rcvr_nok",
    "phy_remote_rcv_nok",
    "phy_lpi_count",
}


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


def _get_phy_statistics(interface: str) -> dict:
    """PHY-level counters (see _PHY_STAT_KEYS above) -- returns only
    whatever keys the driver actually reports, {} if the interface's
    PHY driver doesn't support --phy-statistics at all (e.g. the Pi 3's
    USB smsc95xx adapter) rather than a dict of nulls, so the frontend
    can tell "not supported here" apart from "supported, all zero"."""
    ethtool_bin = shell.find_binary(_ETHTOOL_CANDIDATES)
    if not ethtool_bin:
        return {}
    output = _run([ethtool_bin, "--phy-statistics", interface])
    stats = {}
    for line in output.splitlines():
        match = _PHY_STAT_LINE_RE.match(line)
        if match and match.group(1) in _PHY_STAT_KEYS:
            stats[match.group(1)] = int(match.group(2))
    return stats


def get_link_status(interface: str = "eth0") -> dict:
    link_info = _get_ip_link_info(interface)
    ethtool_info = _get_ethtool_info(interface)
    phy_stats = _get_phy_statistics(interface)

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
        "phy_statistics": phy_stats,
    }
