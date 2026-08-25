#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$REPO_DIR/venv"

sudo apt-get update
sudo apt-get install -y python3-venv python3-pip tcpdump ethtool hostapd dnsmasq arp-scan mtr-tiny nmap nftables

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$REPO_DIR/requirements.txt"

# Allow the LanPi service to capture/send raw packets (LLDP/CDP
# discovery, ARP scan, later packet capture) without running as root
# or needing sudo at request time. Some of these packages install to
# /usr/sbin, which isn't in a normal user's PATH (only sudo's), so
# `command -v` alone can't be trusted to find them here.
find_bin() {
  for candidate in "$@"; do
    [ -x "$candidate" ] && { echo "$candidate"; return 0; }
  done
  command -v "$1"
}
sudo setcap cap_net_raw,cap_net_admin=eip "$(find_bin /usr/bin/tcpdump /usr/sbin/tcpdump tcpdump)"
sudo setcap cap_net_raw,cap_net_admin=eip "$(find_bin /usr/sbin/arp-scan /usr/bin/arp-scan arp-scan)"
# nmap doesn't get its MAC-address/vendor reporting from setcap alone --
# confirmed live it checks geteuid()==0, not just raw-socket capability
# (unlike tcpdump/arp-scan above) -- so backend/tools/ip_scanner.py runs
# it via sudo instead, same _run_privileged pattern as nmcli elsewhere.

# Rule 3 (ARCHITECTURE.MD): never bridge/route between wlan0 (management)
# and eth0 (TEST PORT), including via the fallback AP's NAT.
sudo cp "$REPO_DIR/system/99-lanpi-no-forward.conf" /etc/sysctl.d/99-lanpi-no-forward.conf
sudo sysctl -p /etc/sysctl.d/99-lanpi-no-forward.conf

# Rule 7 (ARCHITECTURE.MD): block LanPi's own management port (8000)
# from eth0 (the TEST PORT), while deliberately leaving SSH open there
# as a recovery path. Takes over /etc/nftables.conf entirely (this is
# a single-purpose device) -- restart is safe/idempotent even if
# nftables.service was already running with this same ruleset loaded.
sudo cp "$REPO_DIR/system/nftables.conf" /etc/nftables.conf
sudo systemctl enable nftables.service
sudo systemctl restart nftables.service

# Remove a stale NetworkManager-hotspot AP profile from earlier LanPi
# versions, if present. NM's built-in Wi-Fi hotspot mode fails WPA2
# handshakes with iOS clients (a known NetworkManager/wpa_supplicant
# interop bug), so the fallback AP is now hostapd-based instead.
if command -v nmcli >/dev/null; then
  nmcli -t -f NAME connection show | grep -qx "LanPi-AP" && \
    sudo nmcli connection delete LanPi-AP || true
fi

# hostapd/dnsmasq are only ever started by lanpi-ap-up.sh (never at
# boot directly) -- Debian masks hostapd.service by default until
# configured, and dnsmasq.service enables itself on install, so both
# need to be explicitly brought under our control here.
sudo systemctl unmask hostapd
sudo systemctl disable hostapd dnsmasq 2>/dev/null || true
sudo systemctl stop hostapd dnsmasq 2>/dev/null || true

sudo cp "$REPO_DIR/system/dnsmasq-ap.conf" /etc/dnsmasq.conf

# hostapd's config carries the AP password, so it's templated rather
# than committed to git. Generated once on first install only; not
# stored anywhere else, and not regenerated on re-install.
if [ ! -f /etc/hostapd/hostapd.conf ]; then
  AP_SSID="LanPi"
  AP_PASSWORD="$(python3 -c 'import secrets, string; print("".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(16)))')"

  sed "s/__AP_PASSWORD__/$AP_PASSWORD/" "$REPO_DIR/system/hostapd.conf.template" | \
    sudo tee /etc/hostapd/hostapd.conf > /dev/null
  echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' | sudo tee /etc/default/hostapd > /dev/null

  echo "=============================================="
  echo "LanPi fallback AP created (write this down now,"
  echo "it will not be shown again):"
  echo "  SSID:     $AP_SSID"
  echo "  Password: $AP_PASSWORD"
  echo "  Address:  172.24.58.1"
  echo "=============================================="
else
  echo "/etc/hostapd/hostapd.conf already exists, leaving it untouched."
fi

# TEST PORT (eth0) Rule 4: passive by default at every boot. NM simply
# leaving eth0 with no active connection already achieves this, so all
# we need is for nothing to auto-activate on it -- including whatever
# ethernet profile the OS itself generated (e.g. a netplan-imported
# one with autoconnect=yes/DHCP), which would otherwise silently
# violate Rule 4. Our own lanpi-eth0 profile (used by DHCP/Static mode
# switching, see backend/network/eth0_mode.py) is created with
# autoconnect=no for the same reason.
if command -v nmcli >/dev/null; then
  nmcli -t -f NAME,TYPE connection show | awk -F: '$2=="802-3-ethernet" {print $1}' | \
    while read -r conn_name; do
      sudo nmcli connection modify "$conn_name" autoconnect no
    done
  nmcli -t -f NAME connection show | grep -qx "lanpi-eth0" || \
    sudo nmcli connection add type ethernet ifname eth0 con-name lanpi-eth0 \
      autoconnect no ipv4.method auto ipv4.never-default yes
fi

sudo chmod +x "$REPO_DIR/system/lanpi-ap-up.sh" "$REPO_DIR/system/lanpi-ap-down.sh"
sudo chmod +x "$REPO_DIR/system/lanpi-wifi-fallback.sh"
sudo cp "$REPO_DIR/system/lanpi-wifi-fallback.service" /etc/systemd/system/lanpi-wifi-fallback.service
sudo systemctl daemon-reload
sudo systemctl enable lanpi-wifi-fallback.service

sudo cp "$REPO_DIR/system/lanpi.service" /etc/systemd/system/lanpi.service
sudo systemctl daemon-reload
sudo systemctl enable lanpi.service
sudo systemctl restart lanpi.service

# Passwordless sudo for the unattended service (see backend/shell.py
# run_privileged()) -- validated with visudo -cf on a scratch file
# BEFORE it ever touches /etc/sudoers.d, since a malformed file placed
# there directly could break sudo for everyone.
SUDOERS_TMP="$(mktemp)"
sed "s|__REPO_DIR__|$REPO_DIR|g" "$REPO_DIR/system/lanpi-backend-sudoers.template" > "$SUDOERS_TMP"
sudo visudo -cf "$SUDOERS_TMP"
sudo install -m 0440 -o root -g root "$SUDOERS_TMP" /etc/sudoers.d/lanpi-backend
rm -f "$SUDOERS_TMP"

echo "LanPi installed. Check status with: sudo systemctl status lanpi.service"
