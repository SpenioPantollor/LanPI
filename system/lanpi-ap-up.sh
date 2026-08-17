#!/usr/bin/env bash
# Switch wlan0 from NetworkManager client mode into LanPi's fallback
# access point: hostapd (beacon/auth) + dnsmasq (DHCP + DNS hijack) +
# an nftables rule redirecting HTTP to LanPi's own dashboard, so any
# connecting device gets a captive-portal-style automatic pop-up.
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

nft add table ip lanpi_ap 2>/dev/null || true
nft add chain ip lanpi_ap prerouting '{ type nat hook prerouting priority -100 ; }' 2>/dev/null || true
nft add rule ip lanpi_ap prerouting iifname "wlan0" tcp dport 80 redirect to :8000 2>/dev/null || true

echo "LanPi fallback AP is up."
