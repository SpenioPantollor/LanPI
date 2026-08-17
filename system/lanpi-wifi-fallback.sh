#!/usr/bin/env bash
# Continuously monitors wlan0. If it isn't connected to a known network
# for more than DOWN_THRESHOLD seconds and the fallback AP isn't already
# active, brings up LanPi's fallback AP so the device stays reachable.
#
# This used to be a one-shot boot-time check (wait up to 25s, then
# exit). That missed the realistic field case: the Pi boots fine on a
# known network at the office, then gets carried somewhere with no
# known network while already running -- the AP never triggered
# because the check had already exited. This keeps watching for the
# life of the service instead.
#
# Deliberately one-directional: once the AP is up, this script leaves
# it running rather than trying to detect a known network coming back
# into range and tearing the AP down automatically -- that would mean
# either kicking whatever's currently connected to the AP to go test
# wlan0, or reasoning about state indirectly, both riskier than the
# trigger-forward case. Returning to normal Wi-Fi after using the
# fallback AP is a deliberate action (system/lanpi-ap-down.sh).
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INTERFACE="wlan0"
CHECK_INTERVAL=5
DOWN_THRESHOLD=25

wlan0_connected() {
  local state
  state="$(nmcli -t -f GENERAL.STATE device show "$INTERFACE" 2>/dev/null | cut -d: -f2)"
  [[ "$state" == "100 (connected)" ]]
}

ap_active() {
  systemctl is-active --quiet hostapd
}

down_seconds=0

while true; do
  if ap_active || wlan0_connected; then
    down_seconds=0
  else
    down_seconds=$((down_seconds + CHECK_INTERVAL))
    if (( down_seconds >= DOWN_THRESHOLD )); then
      echo "LanPi: no known Wi-Fi network for ${down_seconds}s, starting fallback AP."
      "$REPO_DIR/system/lanpi-ap-up.sh" || echo "LanPi: failed to start fallback AP" >&2
      down_seconds=0
    fi
  fi
  sleep "$CHECK_INTERVAL"
done
