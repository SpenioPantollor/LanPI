#!/usr/bin/env bash
# Switch wlan0 back from LanPi's fallback AP to NetworkManager client
# mode, which will auto-reconnect to any known network in range.
set -euo pipefail

nft delete table ip lanpi_ap 2>/dev/null || true

systemctl stop dnsmasq
systemctl stop hostapd

nmcli device set wlan0 managed yes
sleep 2
# The static AP address doesn't get cleared automatically when
# NetworkManager takes wlan0 back over.
ip addr del 172.24.58.1/24 dev wlan0 2>/dev/null || true

echo "LanPi fallback AP is down, wlan0 returned to NetworkManager."
