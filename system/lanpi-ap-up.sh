#!/usr/bin/env bash
# Switch wlan0 from NetworkManager client mode into LanPi's fallback
# access point: hostapd (beacon/auth) + dnsmasq (DHCP), reachable at
# the fixed address 172.24.58.1:8000.
#
# No captive-portal DNS hijack or HTTP redirect here (removed --
# maintainer's call, 2026-08-22): it caused more problems than it
# solved (broke real DNS lookups for anyone using the fallback AP for
# something other than reaching LanPi, and the auto-popup it was for
# never reliably worked on iOS anyway, see STATUS.md).
#
# NetworkManager's own built-in Wi-Fi hotspot mode was tried first and
# rejected: it fails WPA2 4-way handshakes with iOS clients
# ("Incorrect Password" even with the right password) -- a known
# NetworkManager/wpa_supplicant-hotspot-vs-Apple interop issue. hostapd
# is a full, spec-compliant AP implementation and doesn't have it.
set -euo pipefail

AP_SUBNET="172.24.58.1/24"

nmcli device set wlan0 managed no
ip addr flush dev wlan0
ip addr add "$AP_SUBNET" dev wlan0
ip link set wlan0 up

systemctl start hostapd
systemctl start dnsmasq

echo "LanPi fallback AP is up."
