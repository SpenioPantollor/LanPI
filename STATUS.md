# LanPi — Project Status

Last updated: 2026-08-17

This file tracks what has actually been built and deployed, as a
day-to-day companion to the long-term plan in `ARCHITECTURE.MD`.

## Summary

**V0.1 is feature-complete** (README roadmap): eth0 link status, eth0
IP mode switching (Passive/DHCP/Static), LLDP, CDP, ARP scan, Ping,
TCP port test, and PCAP capture are all implemented and verified
against real hardware. Wi-Fi client management and the fallback access
point ("remote management" — reach LanPi's own web UI in the field
with no other network available) are implemented, **live-tested
end-to-end including a real iPhone joining the AP**, and have a
dedicated Settings page. Hardware system stats (CPU/RAM/temp/disk)
round out the dashboard.

**V0.2 in progress**: MNDP discovery, MTR/traceroute, and richer DHCP
lease info are done and live-verified. IP scanner, port scanner,
traffic statistics, and top talkers are still open.

**Note on git history**: squashed to a single commit on 2026-08-17 and
force-pushed, intentionally discarding all prior commit history.
Earlier commits had, at various points, included real internal IPs
and a device/switch name later scrubbed in subsequent commits -- but
still recoverable from history until this squash. If this file or
`ARCHITECTURE.MD` references "an earlier commit" or "history shows..."
from before this date, that history no longer exists.

## Hardware / Deployment

| Item | Value |
| --- | --- |
| Device hostname | `LanPI` |
| Management IP (wlan0, client mode) | reachable as `lanpi.local` / SSH host `lanpi` (see local SSH config) |
| Recovery IP (eth0, DHCP) | manually brought up as a safety net during Wi-Fi testing — not persistent, only up if manually activated |
| OS | Raspberry Pi OS (Debian trixie, aarch64), kernel `6.18.39+rpt-rpi-v8` |
| Service | `lanpi.service` (systemd), enabled at boot, running |
| Backend URL | `http://<wlan0-ip>:8000/` (client mode) or `http://172.24.58.1:8000/` (fallback AP mode) |
| Test setup | `eth0` connected to an **unmanaged** switch (a second device is also attached to it); that switch currently uplinks into the same LAN as `wlan0` |
| journald | persistent logging enabled (`/var/log/journal`) after a real unexplained reboot left no logs to diagnose it from — was volatile/in-memory-only before |

## What works today

- **Backend**: FastAPI app (`backend/main.py`) serving the frontend as
  static files and exposing:
  - `GET /api/health`, `GET /api/status` — liveness, hostname,
    platform, version, backend uptime
  - `GET /api/network/eth0` — link state, speed, duplex, autoneg, MAC,
    MTU, RX/TX counters, via `ip -j -s link` and `ethtool`
    (`backend/network/link.py`)
  - `GET/POST /api/network/eth0/mode` — Passive/DHCP/Static IP mode for
    the TEST PORT (`backend/network/eth0_mode.py`), via a dedicated
    `lanpi-eth0` nmcli profile. Passive is the true default: `install.sh`
    disables autoconnect on every ethernet profile bound to eth0
    (including whatever the OS itself generated), so eth0 comes up with
    no IP and no L3 traffic at every boot (ARCHITECTURE.MD Rule 4).
  - `GET /api/discovery/lldp` — passive LLDP neighbor discovery
    (`backend/discovery/lldp.py`): background thread runs `tcpdump`
    continuously, parses LLDP TLVs from its pcap stream, caches the
    latest neighbor. Runs unprivileged via `setcap
    cap_net_raw,cap_net_admin` on `tcpdump`, no root/sudo needed.
  - `GET /api/discovery/cdp` — same background-tcpdump-plus-cache
    architecture as LLDP (`backend/discovery/cdp.py`), adapted for
    CDP's 802.3+LLC/SNAP framing (dest MAC `01:00:0c:cc:cc:cc`, OUI
    `00:00:0c`, PID `0x2000`) instead of LLDP's plain EtherType.
    Parses device ID, port ID, platform, software version, native
    VLAN, address.
  - `GET /api/discovery/mndp` — same architecture again
    (`backend/discovery/mndp.py`), for MikroTik's MNDP: UDP broadcast
    (port 5678) rather than an L2 frame, so framing needs the IPv4
    header's variable length read from the packet itself
    (`(byte[14] & 0x0F) * 4`) before the UDP/MNDP payload starts.
    Parses identity, platform, board, RouterOS version, uptime,
    software ID, interface name, IPv4/MAC address.
  - `POST /api/tools/mtr` — single-shot MTR report
    (`backend/tools/mtr.py`) sourced from eth0's address (same
    default-route reasoning as the TCP port test below). Uses `mtr
    --report --json` and parses the JSON directly rather than
    scraping mtr's text table.
  - `POST /api/tools/arp-scan` — active host discovery on eth0's local
    network via `arp-scan` (`backend/tools/arp_scan.py`); uses
    `--localnet` when eth0 has an address, or an explicit network
    (works from Passive mode too). Unprivileged via the same
    `setcap cap_net_raw,cap_net_admin` pattern as `tcpdump`.
  - `GET/POST /api/network/wifi*` — Wi-Fi client status/scan/connect/
    forget via `nmcli` (`backend/network/wifi.py`). Only ever touches
    `wlan0`.
  - `GET /api/system` — CPU utilization/temp, load average, memory,
    disk, Pi model, system uptime, read directly from `/proc`/`/sys`
    (`backend/tools/system_info.py`, no extra dependency)
  - `POST /api/tools/ping/start`, `GET /api/tools/ping/status`,
    `POST /api/tools/ping/stop` — background continuous ping
    (`backend/tools/ping.py`): `count` is optional (blank = runs until
    stopped), `received`/`replies` update live per-reply, stop sends
    SIGINT (not SIGTERM) so `ping` prints its usual summary line before
    exiting, which is what fills in the final `transmitted`/
    `packet_loss_percent`. `transmitted` stays `null` while still
    running since ping doesn't emit anything for a lost/pending packet
    to count from.
  - `GET/POST /api/network/ap` — fallback AP status and SSID/password
    config, backed by `/etc/hostapd/hostapd.conf`
    (`backend/network/ap.py`).
  - Any unmatched path (404) redirects to `/` instead of erroring —
    makes the fallback AP behave like a captive portal (see below).
- **Fallback access point — hostapd-based**: SSID `LanPi`,
  non-standard subnet `172.24.58.1/24`, WPA2-PSK.
  `system/lanpi-ap-up.sh` / `lanpi-ap-down.sh` switch `wlan0` between
  NetworkManager client mode and a manually-managed hostapd + dnsmasq
  AP (dnsmasq also does a wildcard DNS hijack + an nftables port-80
  redirect, so any URL a joining device tries to load lands on LanPi's
  dashboard automatically, like a public Wi-Fi captive portal).
  `system/lanpi-wifi-fallback.service` continuously monitors `wlan0`
  (checks every 5s) and runs `lanpi-ap-up.sh` whenever it's had no
  known network for 25+ seconds — **not** a one-shot boot check
  anymore. The original one-shot version (wait up to 25s at boot, then
  exit for good) missed the realistic field case: boot fine on a known
  network, then get carried somewhere with no known network while
  already running — the AP never triggered because the check had
  already exited. Deliberately one-directional still: it doesn't
  auto-tear-down the AP when a known network comes back into range
  (see `add_known()` below for the one path that does bring it down,
  on purpose, from a user action).
  **Important history**: the first implementation used
  NetworkManager's own built-in Wi-Fi hotspot mode (`ipv4.method
  shared`) — live-tested against a real iPhone, it consistently failed
  the WPA2 4-way handshake ("Incorrect Password" regardless of the
  actual password, ruled out via QR-code join and PMF/cipher tweaks).
  This is a known NetworkManager/wpa_supplicant-hotspot interop bug
  with Apple devices specifically (Android/Linux clients aren't
  affected). Switched to hostapd (a full, spec-compliant AP daemon)
  and the iPhone connected successfully on the first try. Don't
  reintroduce the NM-hotspot approach.
  `system/99-lanpi-no-forward.conf` disables IP forwarding as
  defense-in-depth for ARCHITECTURE.MD Rule 3 (no bridge between
  `wlan0` and `eth0`), independent of hostapd/dnsmasq internals.
- **Frontend**: two pages now.
  - **Dashboard** (`/`, `index.html` + `app.js`): Backend, System
    (CPU/RAM/temp/disk), Test Port (eth0, incl. IP mode controls),
    Neighbor (LLDP), Neighbor (CDP), ARP Scan, a compact read-only
    Wi-Fi status card linking to Settings, and Ping. Polls every 5s
    plus an immediate refresh on `visibilitychange` (mobile browsers
    suspend timers while backgrounded, especially "add to home
    screen" standalone mode — without this the page shows stale data
    until manually reloaded). Footer with copyright/year and live
    date/time.

    Cards lay out via a small hand-rolled JS masonry (`layoutCards()`
    in `app.js`/`settings.js`), after both pure-CSS options failed
    live: Grid forces uniform row height, stranding shorter cards
    below a tall neighbor with a big gap (reported after Test Port
    grew its IP-mode controls); CSS multi-column ("masonry") packs by
    shortest-current-column, which reflows *every* card's position
    whenever any one card's height changes, not just cards below it
    (reported after an ARP scan). The JS version assigns each card a
    FIXED column by DOM order (`i % columns`) — a card's column never
    changes due to another card's height, only its offset within its
    own column does, and only that column's later cards shift as a
    result. `ResizeObserver` on every card triggers relayout
    automatically on any content change (ARP results, ping replies,
    new LLDP/CDP neighbors, etc.); column count itself only changes on
    an actual window-width breakpoint crossing.
  - **Settings** (`/settings.html` + `settings.js`): Wi-Fi client
    scan/connect (password prompt)/saved-network list+forget, an "Add
    known network" form, and fallback AP SSID/password editing (writes
    hostapd.conf, restarts hostapd immediately if it's currently active
    — warns that this disconnects anyone currently on the fallback AP).
  - Dark theme, mobile-optimized (fluid type scale, safe-area insets,
    `theme-color` / web-app meta tags).
- **Deployment**: `system/install.sh` provisions the Python venv,
  installs `tcpdump`/`ethtool`/`hostapd`/`dnsmasq`, grants `tcpdump`
  capture capabilities, installs the sysctl no-forwarding rule, masks/
  disables hostapd+dnsmasq from auto-starting (only `lanpi-ap-up.sh`
  starts them), templates a generated AP password into
  `/etc/hostapd/hostapd.conf` on first install only (never committed
  to git), removes any stale NetworkManager AP profile from earlier
  versions, and installs/enables both systemd units. Verified
  end-to-end against the physical Pi, including re-running it
  idempotently on top of an already-configured install.
- **Module layout**: `backend/tools`, `backend/capture` exist as empty
  packages, ready for their turn per `ARCHITECTURE.MD`.

## Verified against real hardware

- `eth0` link status: confirmed accurate (100 Mbps / full duplex /
  autoneg on against the unmanaged switch).
- LLDP: **fully confirmed working** — after connecting eth0 to a real
  managed router (which, unlike the earlier unmanaged switch, actually
  originates LLDP), a neighbor came through correctly: chassis ID,
  port ID/description, system name/description, management IP all
  populated. (Specific values aren't recorded here since this is a
  real device on the maintainer's own network -- see Hardware section
  above for why real IPs/hostnames are kept out of this repo.)
- Ping: **fully confirmed working** — one-shot (count=6, reached the
  target naturally, correct final stats), continuous (no count,
  stopped manually via the API, SIGINT path produced correct final
  stats too), and an unreachable target (100% loss reported correctly).
- Fallback AP: **fully confirmed working**, including a real iPhone
  joining, getting a DHCP lease, and reaching the dashboard at
  `172.24.58.1:8000`. AP-up/AP-down cycle tested repeatedly without
  losing the ability to recover (see NM-hotspot-vs-hostapd note above
  for how the working config was reached).
- Captive-portal auto-redirect: backend-side 404→dashboard redirect
  and the DNS-hijack+port-80-redirect are deployed and individually
  verified (curl), but the full "join AP → page pops up automatically"
  flow wasn't confirmed on iOS in this session — iOS treats the AP as
  a no-internet network and restricts background traffic, so it may
  require opening Safari manually even though the redirect is in
  place. Not yet retested.
- eth0 IP mode: **fully confirmed working** — cycled Passive → DHCP →
  Static → Passive live against the deployed Pi via the API. Two real
  bugs found and fixed along the way:
  - `nmcli` state-changing calls (`device disconnect`,
    `connection up/modify`) fail with "not authorized" when run by the
    unprivileged service user (no active login session for polkit to
    grant `network-control` to) — fixed by routing those specific
    calls through `sudo` (passwordless for this user), same pattern
    already used in `ap.py`. The same fix was applied preemptively to
    `wifi.py`'s `connect`/`forget`, which have the same polkit
    requirement.
  - Switching Static → DHCP left the old `ipv4.addresses`/`gateway` set
    on the `lanpi-eth0` profile (`ipv4.method` alone doesn't clear
    them), so NetworkManager kept applying the stale static address
    instead of the real DHCP lease — `set_dhcp()` now explicitly clears
    them. Worse, that leftover static route (metric 100) **outranked
    wlan0's default route (metric 600) and silently hijacked all of
    the Pi's own outbound traffic**, including its own `git`/`apt`
    access, until manually fixed over SSH. Root-caused and fixed with
    `ipv4.never-default=yes` on `lanpi-eth0`, so the TEST PORT can
    never contribute a default route no matter what gateway it's
    handed — confirmed via `ip route` afterward: eth0 gets DHCP but no
    default-route entry, wlan0 stays the only path out.
  - Separately, `ipv4.never-default` also hides `IP4.GATEWAY` from
    `nmcli device show` entirely (NM doesn't install a gateway route,
    so it doesn't report one), which made the dashboard show a blank
    gateway in DHCP mode even though DHCP had handed one out. Fixed by
    falling back to `DHCP4.OPTION`'s `routers` value (DHCP mode) or
    the connection profile's `ipv4.gateway` (Static mode) for display
    purposes -- the route itself stays correctly suppressed either way.
- CDP: **fully confirmed working** -- a real neighbor came through
  (device ID, port ID, platform, software version, management address
  all populated). Corrects an earlier assumption in this file that
  "MikroTik doesn't originate CDP" since it's not Cisco: it turns out
  MikroTik RouterOS's neighbor-discovery feature sends CDP-compatible
  announcements too, not just its own MNDP. Specific values aren't
  recorded here (real device on the maintainer's own network, see
  Hardware section above).
- ARP scan: **fully confirmed working** -- found multiple real hosts
  (IP/MAC/vendor) on the local network via `--localnet` once eth0 had
  a DHCP address. Values not recorded here (real devices on the
  maintainer's network).
- Ping: status line now keeps showing the last-pinged host after Stop
  instead of resetting to a bare "stopped", since the backend already
  retains it (and the results) until the next Start -- the frontend
  just wasn't displaying it.
- `install.sh`: found and fixed a real bug adding ARP scan --
  `arp-scan` installs to `/usr/sbin`, which isn't in a plain user's
  PATH (only sudo's), so `command -v arp-scan` inside the script
  silently returned nothing, breaking its `setcap` call and, because
  of `set -e`, aborting the rest of the script before it could finish.
  Fixed with a `find_bin` helper that checks known `/usr/sbin`/`/usr/bin`
  locations before falling back to `command -v`.
- Wi-Fi scan: found and fixed a real bug -- `scan()` used the
  unprivileged nmcli call for `--rescan yes`, same polkit issue as the
  eth0-mode bug above. Without privilege, nmcli silently returns only
  the cached/currently-associated network instead of scanning -- user
  reported "always only shows one network", confirmed live (1 network
  unprivileged vs. 7 real nearby networks with sudo).
- Fallback AP "closed loop": found and fixed a real gap -- the Pi has
  a single Wi-Fi radio, so once the fallback AP owns `wlan0`
  (`hostapd`), `scan()`/`connect()` can't do anything with it (device
  is `unmanaged`, owned by hostapd, not NetworkManager). That meant
  reachable only via the fallback AP, but unable to configure the real
  network from it. Fixed with `wifi.add_known()`
  (`POST /api/network/wifi/add-known`): writes an nmcli profile
  without touching the live device (works regardless of AP state),
  and if the AP was active, immediately tears it down and tries to
  bring the new connection up, restoring the AP automatically if that
  fails (wrong password, out of range) so a typo can't strand the
  device. **Fully confirmed live**: tested the failure path with a
  nonexistent SSID (AP came down, 30s connect attempt failed, AP came
  back up automatically, ~31s total) and the success path with a real
  known network from the phone while connected to the fallback AP
  (switched over correctly). Tested safely throughout via the `eth0`
  DHCP recovery IP, which `wlan0` mode changes don't affect.
- Cable diagnostics (TDR pair quality / length): **checked, not
  supported** on the Pi 3 in use -- `ethtool --cable-test eth0` returns
  "PHY driver does not support cable testing" (`ethtool -i eth0` shows
  `driver: smsc95xx`, a USB-attached adapter, not a native PHY). Not
  implemented; see README.md Cable Diagnostics section for the Pi 4
  caveat (plausible but unconfirmed without testing on one).
- TCP port test (`backend/tools/tcp_test.py`, `POST /api/tools/tcp-test`):
  **fully confirmed working** -- binds the outbound socket to eth0's
  current address before connecting (not just any outbound socket),
  since eth0 has no default route by design and an unbound connect()
  to a host outside eth0's subnet would silently go out wlan0 instead.
  Confirmed both `open` (a real listening port on the local network)
  and `timeout` (an unreachable host) outcomes live. Requires DHCP/
  Static mode -- Passive has no source address to bind, returns a
  clear `no_source_ip` error instead of trying.
- Packet capture (`backend/capture/pcap.py`, `/api/capture/*`):
  **fully confirmed working** -- start (duration-based and open-ended),
  manual stop, status while running, list, download (verified the
  output is a real, valid .pcap via `file`), delete, an invalid BPF
  filter surfacing tcpdump's actual parse error instead of silently
  "succeeding", and a path-traversal filename on both download and
  delete correctly rejected. Mirrors `ping.py`'s background-Popen
  design (SIGTERM stops tcpdump cleanly, parallel to ping's SIGINT).
  End-to-end confirmed from the real browser (not just `curl`):
  captured, downloaded, and **opened cleanly in Wireshark with no
  errors**.
- TCP port test: also independently confirmed working from the real
  browser (multiple `POST /api/tools/tcp-test` requests logged) after
  the stale-JS-cache issue below was resolved -- not just from `curl`.
- Found and fixed two more real bugs while building the above:
  - `backend_uptime_seconds` (`/api/status`) used `datetime.now()` to
    compute elapsed time, but the Pi has no battery-backed RTC -- the
    wall clock starts each boot at whatever it last saved on shutdown
    and only steps to the real time once NTP syncs, which happens
    *after* this value gets stamped. Observed live: dashboard showed
    ~60000s of uptime within minutes of a real reboot. Switched to
    `time.monotonic()`, which is immune to wall-clock steps.
  - `main.py`'s captive-portal 404→redirect handler caught **every**
    404 in the app, including from the API itself (found via
    `capture_download`'s deliberate 404 for a missing file) -- scoped
    the redirect to non-`/api/` paths only. Separately, its fallback
    branch for non-matching cases did `raise exc`, which doesn't fall
    through to FastAPI's default exception handling like it looks like
    it should (registering a custom `StarletteHTTPException` handler
    replaces the default one entirely) -- it became an unhandled 500
    instead. Now builds the same `{"detail": ...}` JSON response the
    default handler would.

- Stale-JS bug found and fixed: TCP port test looked completely dead
  (host/port entered, "Test" clicked, page just reloaded) -- root
  cause was `StaticFiles` sending no `Cache-Control` header at all, so
  the browser could keep serving a pre-deploy `app.js` with no visible
  error; without the new submit listener attached, the form fell
  through to a native GET submit, which also explains the reported
  "IP disappears" (same cause, not a separate bug). Fixed with a small
  middleware stamping `Cache-Control: no-cache` on every non-API
  response (forces revalidation via the existing ETag/Last-Modified on
  every load; API responses untouched). Confirmed live via response
  headers before/after.
- Ping card redesigned: dropped the per-reply list, shows live-updating
  min/avg/max RTT instead (running aggregate while ping is active,
  overwritten by ping's own authoritative summary line once it's
  available) plus an explicit lost-packet count next to the loss
  percentage. Confirmed live on the real Pi against a real host for
  all three lifecycle paths: natural completion, mid-run (live
  numbers), and manual stop (SIGINT).
- MNDP: **fully confirmed working on the first deploy** -- real
  neighbor came through with every field populated (identity,
  platform, board, RouterOS version, uptime, software ID, interface
  name, IPv4/MAC address). `identity`/`version`/`interface_name`
  independently matched the same device's already-confirmed CDP output
  exactly, a good cross-check that both parsers are decoding real
  data correctly rather than coincidentally not crashing. TLV type
  values and the little-endian uptime field, both taken from public
  documentation rather than RouterOS source, turned out correct
  without needing any adjustment.
- MTR: **fully confirmed working** -- real 8-hop trace from eth0 to
  8.8.8.8 through the maintainer's actual ISP path, each hop reporting
  sensible loss/last/avg/best/worst numbers. `mtr-packet` (from
  `mtr-tiny`) worked fully unprivileged as the service's own user, no
  `setcap` needed -- unlike `tcpdump`/`arp-scan`, Debian's package
  apparently handles this itself. Originally a single blocking POST
  (could hang up to `cycles+30`s against an unreachable host with no
  way to cancel -- user-reported); converted to start/status/stop like
  `ping.py`. Both paths confirmed live: normal completion (real
  hop data) and mid-run stop (SIGTERM, ~1-2s to actually die, then
  `{running: false, ok: false, message: "stopped"}` -- distinguished
  from a real mtr failure via an internal flag, since a killed
  process's stdout would otherwise just fail JSON parsing and look
  like a random error).
- DHCP lease info: **fully confirmed working** -- `lease_time_seconds`,
  `dhcp_server`, and `domain_name` (null when unset, not an empty
  string) all populated correctly from a real DHCP lease on eth0.

## Known gaps

- No IP scanner, no port scanner, no traffic statistics, no top
  talkers. Still planned per `ARCHITECTURE.MD` section 18.
- No authentication on the web UI or API. Fine for now (LAN-only), but
  a real blocker given the Settings page can change Wi-Fi credentials
  and the fallback AP password, eth0 mode changes can affect routing,
  and packet captures/TCP tests can now be triggered by anyone on the
  LAN (or, notably, anyone already connected to the fallback AP
  itself) over an unauthenticated API.
- No tests.

## Next steps

- V0.2 remaining: IP scanner, port scanner, traffic statistics, top
  talkers -- or closing the auth gap above, whichever the maintainer
  wants first.
- Re-verify the captive-portal auto-open flow with a phone.
