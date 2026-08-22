# LanPi — Project Status

Last updated: 2026-08-22

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

**V0.2 is feature-complete**: MNDP discovery, MTR/traceroute, richer
DHCP lease info, traffic statistics/top talkers, IP scanner, and port
scanner are all done and live-verified, each scanner/analysis tool
with its own dedicated page (Traffic, IP Scanner, Port Scanner)
alongside Dashboard/Settings -- five pages now, navigated via a
pill-button tab bar. A "passive device discovery" stretch item was
proposed and then dropped (2026-08-17): the Traffic page's Top
Talkers table already aggregates every passively-observed device
(IP/MAC + protocol breakdown), so a separate unified-device-list page
would have been substantially redundant with it.

**V0.3 (industrial Ethernet support) started 2026-08-17**: Modbus TCP
read client (coils/discrete inputs/holding/input registers) done,
protocol logic (connect/read/parse) confirmed against **a real Modbus
TCP slave on the network**, not just the local test server used
during development. The Kamstrup device templates (specific register
addresses, float32 word order) are still unconfirmed against an
actual Kamstrup meter, since that slave wasn't one -- protocol
mechanics and register-map accuracy are separate claims. PROFINET
DCP/traffic detection, S7 diagnostics, and industrial device ID are
still open, deliberately deferred until real PLC hardware is available
(maintainer's call, 2026-08-21) -- not started in this pass.

**v0.2.4 (Modbus TCP diagnostics expansion) complete as of 2026-08-22**,
per a maintainer-provided implementation brief expanding basic Modbus
read into a full diagnostic toolset: Device Identification (FC43/
MEI14), an exception decoder (already existed, confirmed), a register
data-interpretation helper (16/32-bit, all 4 byte orders), response
timing + raw request/response view, a Unit ID scanner, a
bisection-based register range scanner, live polling with
communication statistics, and passive Modbus TCP traffic analysis
built on the v0.2.3 shared capture dispatcher. Device Registry
integration (brief item #10) was explicitly skipped -- its dependency,
a unified device registry, doesn't exist yet (see the V0.3 backlog in
README) -- per the maintainer's instruction to do everything in the
brief except what depends on something planned for a later version.
All of it live-verified against the real Modbus TCP slave at
192.168.88.21 (see Verified section below), including the passive
analyzer correctly correlating every request/response pair generated
by the other live tests in the same session, exception counting, and
per-unit-ID/function-code relationship tracking.

**Link event history (V0.3 backlog item) complete as of 2026-08-22**:
a background poller (`backend/network/link_history.py`) checks eth0's
link snapshot every 2s and logs an event only when presence/operstate/
link_detected/speed/duplex change, never on RX/TX byte counters alone.
Live-verified on the Pi against a real cable pull and replug (see
Verified section below) -- a genuine DOWN event on unplug and a UP
event with the correct restored speed/duplex on replug, with no
spurious entries from steady-state traffic in between. Chosen first
off the V0.3 backlog (maintainer's call, 2026-08-22) over duplicate-IP
detection, rogue-DHCP detection, and the unified device registry.

**Duplicate-IP and rogue-DHCP detection (V0.3 backlog items) complete
as of 2026-08-22**: two more passive listeners on the shared capture
dispatcher. `backend/capture/ip_conflict.py` tracks, per IP, every MAC
address claiming it via ARP (both requests and replies count as a
claim); an IP claimed by 2+ MACs at once is reported as a conflict,
and a claim expires after 10 minutes of silence so a resolved
reassignment clears itself. `backend/capture/dhcp_monitor.py` tracks
every distinct DHCP server seen answering OFFER/ACK on the segment
(keyed by the DHCP server-identifier option) and flags
`multiple_servers_detected` the moment a second one appears -- there's
no known-good-server list to compare against (no device registry
exists to hold one), so it reports what it sees and leaves the
judgment call to the operator. Both live-verified on the Pi (see
Verified section below), including a genuinely interesting real
finding: cycling eth0 through Passive→DHCP correctly captured the real
DHCPACK from the real gateway (192.168.88.1) with the exact leased
address, and the IP-conflict tracker correctly flagged a real ARP
conflict -- between the Pi's own eth0 and wlan0 MACs both claiming
192.168.88.149, an artifact of this development rig's eth0 TEST PORT
being plugged into the same LAN segment as wlan0 rather than a
genuinely isolated test network. Not a bug: two real MACs really were
claiming the same real IP on the wire the capture sees, which is
exactly what the detector is supposed to catch -- it says nothing
about whether the two claimants are hostile, just that operators
should look. Deliberately not filtered out (unlike traffic_stats.py's
self-MAC exclusion for its unrelated "who's talking" view) since a
real deployment where LanPi's own TEST PORT gets handed an
already-in-use address is exactly the kind of conflict this feature
exists to catch.

**V0.2.3 (foundation hardening) complete as of 2026-08-21**, per a
maintainer-provided refactoring brief: the goal was making the
existing V0.1/V0.2 code more robust before building further
industrial-protocol features on top of it, not adding new user-facing
functionality. All 8 agreed items done and live-verified: centralized
versioning + pinned dependencies, automated tests, management-interface
isolation, capture storage limits, route-file splitting, the shared
capture dispatcher + health reporting, the shared command-execution
helper, and CI. See README's Version 0.2.3 roadmap section for the
full checklist. `eth0` was deliberately kept as a management/recovery
path (SSH) even after interface isolation, on the maintainer's
explicit pushback against the brief's original item 1 -- only port
8000 gets blocked there, not port 22. Four brief items (unified device
registry, link event history, duplicate IP detection, rogue DHCP
server detection) were reclassified as new features rather than
foundation hardening and moved to the V0.3 backlog instead (see
README).

- Centralized version management: single `VERSION` file at the repo
  root, read by `backend/version.py`, consumed by both FastAPI's own
  metadata and `/api/status`'s `lanpi_version` field -- confirmed live
  (`{"lanpi_version": "0.2.3", ...}`). Fixes a real prior bug: `main.py`
  and `routes.py` each had their own hardcoded version string, one of
  which had drifted stale.
- Pinned dependencies: `requirements.txt` constrained to `fastapi~=0.141.1`
  / `uvicorn~=0.52.3`, matching what's actually installed and verified
  on the Pi (confirmed `pip install -r requirements.txt` resolves
  cleanly there, Python 3.13). Note: these pins can't be installed at
  all in a local venv on Python 3.9 (older macOS) -- FastAPI 0.141
  requires a newer Python, so pip's resolver silently finds no
  matching version. Not a bug in the constraint; the Pi (the real
  target, Python 3.13) is what matters and works.
- Automated tests: `pytest` suite added under `tests/` (99 tests, all
  passing) -- the hand-rolled LLDP/CDP/MNDP TLV parsers (synthetic
  frames, no tcpdump needed), the traffic-stats classifier including
  this session's talker-merge and self-MAC-exclusion logic, the Modbus
  TCP client against a local fake server (protocol framing/exception
  decoding, independent from the real-hardware verification above),
  Modbus device-template float32 decoding, `eth0` mode's nmcli-output
  parsing (mocked, no real nmcli needed), port/IP scanner input
  validation, and FastAPI-layer request validation via `TestClient`.
  All of it runs without a real Pi, `eth0` interface, or network
  device -- pure logic and mocked I/O only. `requirements-dev.txt`
  (`pytest` + `httpx`, the latter needed by FastAPI's `TestClient`)
  keeps these dev-only deps out of the production `requirements.txt`.
- Management interface isolation: `nftables` rule (`system/nftables.conf`,
  ARCHITECTURE.MD Rule 7) blocks TCP/8000 on `eth0` only -- **confirmed
  live**: `curl` to `<eth0-ip>:8000` times out, `curl` to `<wlan0-ip>:8000`
  still returns 200, and SSH to the `eth0` IP directly still works. Per
  the maintainer's explicit pushback on the original brief, SSH was
  deliberately left open on `eth0` rather than isolating it entirely --
  it's the recovery path if `wlan0` becomes unreachable.
- Capture storage limits: a running capture rotates into a new
  `lanpi-<started>-<part>.pcap` file (clean SIGTERM + respawn, not
  tcpdump's own `-C`, which breaks the `.pcap` extension) once the
  active file crosses ~100MB, and `_prune_oldest()` deletes the
  oldest saved captures (by mtime) whenever the total exceeds ~1GB,
  run before every new session and after every rotation/stop --
  **confirmed live** against real tcpdump capturing real `eth0`
  traffic: with thresholds temporarily lowered (100 bytes/20KB) for
  the test, a real capture rotated 001 -> 002 -> 003 as expected, and
  starting a second session correctly pruned the oldest file from the
  first session to stay under the (lowered) total cap, while the
  actively-written file was never touched.
- Route file split + shared capture dispatcher + health reporting:
  `backend/api/routes.py` (339 lines) split into
  `backend/api/routes/{health,system,network,discovery,tools,modbus,
  capture,traffic}.py` -- **confirmed live**, all 40 endpoints
  byte-identical to before (diffed `openapi()`'s path list). LLDP/CDP/
  MNDP/Traffic Stats consolidated onto one shared `tcpdump` process
  (`backend/capture/dispatcher.py`) instead of four separate ones --
  **confirmed live**: `ps aux` on the Pi showed exactly one
  `tcpdump -i eth0 -U -nn -w -` process after restart (was four
  before), and all three discovery endpoints correctly populated from
  a real MikroTik neighbor (`SilainiaiMikrotik`, RouterOS 7.23.3)
  through the shared feed -- LLDP with full chassis/port/system
  description, CDP with device_id/platform/software_version, MNDP
  with identity/board/uptime, all matching what direct per-protocol
  tcpdump processes used to report. `/api/status`'s new
  `capture_dispatcher` field confirmed live too
  (`capture_running: true`, `seconds_since_last_packet` updating).
- Shared command-execution helper (`backend/shell.py`): consolidates
  ~12 near-identical binary-discovery/subprocess.run copies across
  `backend/network/{ap,eth0_mode,link,wifi}.py`,
  `backend/tools/{arp_scan,ip_scanner,mtr,ping,port_scanner,
  system_info}.py`, and `backend/capture/{dispatcher,pcap}.py` into
  `find_binary()`/`run()`/`run_privileged()` -- **confirmed live**
  after redeploy: link status (`ip`/`ethtool`), eth0 mode (`nmcli`),
  Wi-Fi status (`nmcli`), ARP scan (real 14-host scan with vendors),
  ping, MTR, and system/power status (`vcgencmd`) all re-verified
  working against real hardware/network. The fallback AP's
  activate/deactivate path (`ap.py`) uses the identical
  `run_privileged()` pattern already proven by the above, but wasn't
  independently live-flipped this pass (disruptive to `wlan0` to test
  casually -- see `wlan0` caution elsewhere in this file). Also fixed
  a latent bug while migrating: `ap.py`'s `set_config()` used
  `systemctl` in a subprocess call with no guard if it wasn't found.
- CI: `.github/workflows/tests.yml` runs `pytest` on every push to
  `main` and every PR (Python 3.13, matching the Pi) -- **confirmed
  green** on the first push (`gh run list` showed `completed success`
  within 20 seconds).

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
  - `GET /api/network/eth0/history`, `POST .../reset` — link event
    history (V0.3 backlog item, done 2026-08-22):
    `backend/network/link_history.py` polls the same snapshot above
    every 2s from a background thread and appends an event only when
    presence/operstate/link_detected/speed/duplex actually change, not
    on every poll (RX/TX counters are excluded on purpose, or every
    poll would add a spurious entry). Bounded to the last 500 events.
  - `GET /api/network/ip-conflicts`, `POST .../reset` — duplicate-IP
    detection (V0.3 backlog item, done 2026-08-22):
    `backend/capture/ip_conflict.py` tracks every MAC address claiming
    each IP via passively-observed ARP traffic; an IP claimed by 2+
    MACs at once is a conflict. Claims expire after 10 minutes.
  - `GET /api/network/dhcp-servers`, `POST .../reset` — rogue-DHCP
    detection (V0.3 backlog item, done 2026-08-22):
    `backend/capture/dhcp_monitor.py` tracks every distinct DHCP server
    seen answering OFFER/ACK on the segment; `multiple_servers_detected`
    flips true the moment a second one appears. No known-good-server
    list -- reports what it sees, operator judges which belongs.
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
  - `POST /api/tools/mtr/start`, `GET /api/tools/mtr/status`,
    `POST /api/tools/mtr/stop` — MTR report (`backend/tools/mtr.py`)
    sourced from eth0's address (same default-route reasoning as the
    TCP port test below). Uses `mtr --report --json` and parses the
    JSON directly rather than scraping mtr's text table. Background
    start/status/stop (mirrors `ping.py`) so a run against an
    unreachable host can be cancelled instead of blocking; stop kills
    mtr's whole process group (see Verified section for why just the
    tracked process wasn't enough).
  - `GET /api/traffic/stats`, `POST /api/traffic/reset` — passive
    traffic statistics and top talkers (`backend/capture/traffic_stats.py`),
    own dedicated (unfiltered) tcpdump parsing every packet's
    Ethernet/IPv4/ARP headers plus UDP/TCP ports. Served on its own
    Traffic page (`frontend/traffic.html`/`traffic.js`), not a
    dashboard card -- too many columns for that.
  - `POST /api/tools/ip-scan/start`, `GET /api/tools/ip-scan/status`,
    `POST /api/tools/ip-scan/stop` — nmap `-sn` ping-sweep host
    discovery (`backend/tools/ip_scanner.py`), sourced from eth0
    (`-e eth0`, needs an address). Background start/status/stop like
    `mtr.py`, but streams hosts live as nmap reports each one (its
    plain output is naturally line-by-line, unlike mtr's --json which
    only appears at the end). Run via `sudo`, not `setcap` -- nmap's
    MAC/vendor reporting needs real root, confirmed live (see
    Verified section). Own IP Scanner page
    (`frontend/ip-scanner.html`/`ip-scanner.js`).
  - `POST /api/tools/port-scan/start`, `GET /api/tools/port-scan/status`,
    `POST /api/tools/port-scan/stop` — nmap SYN scan (`-sS`) across a
    port range on one host (`backend/tools/port_scanner.py`), sourced
    from eth0 via sudo (same reasoning as IP scanner -- `-sS` needs
    real root). `-Pn` skips nmap's own host-discovery ping so devices
    that don't answer ICMP but do have open ports still get scanned.
    Background start/status/stop, but results only appear once the
    scan finishes (nmap's port table isn't streamed per-port the way
    IP scanner's host discovery is) -- same shape as `mtr.py`,
    including the `_stopped`-flag distinction between a cancelled run
    and an actual failure. Own Port Scanner page
    (`frontend/port-scanner.html`/`port-scanner.js`) with Well-known/
    1-10000/All(1-65535) range presets.
  - `POST /api/tools/modbus/read` — Modbus TCP read
    (`backend/tools/modbus.py`), hand-rolled (MBAP header + PDU) --
    no `pymodbus` dependency, same reasoning as the LLDP/CDP/MNDP
    parsers: simple enough not to need a library for just the four
    read functions (coils, discrete inputs, holding/input registers).
    Read-only by design (Safety section, ARCHITECTURE.MD) -- no write
    functions. Sourced from eth0's address via socket bind. Single
    request/response, not background start/stop (bounded by its own
    3s timeout, no long-running-scan risk to manage). Own Modbus page
    (`frontend/modbus.html`/`modbus.js`).
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
- **Frontend**: six pages now (Dashboard, Traffic, IP Scanner, Port
  Scanner, Modbus, Settings), navigated via a pill-button tab bar in the header (each
  page's `<nav>` lists every page including itself, marked `.active`
  — replaced plain inline text links after user feedback that they
  read as an ambiguous run-on once there were more than two, with
  more pages planned).
  - **Dashboard** (`/`, `index.html` + `app.js`): Backend, System
    (CPU/RAM/temp/disk), Test Port (eth0, incl. IP mode controls),
    Neighbor (LLDP/CDP/MNDP), ARP Scan, MTR/Traceroute, TCP Port Test,
    Packet Capture, Ping (min/avg/max RTT, not a per-reply list), and
    a compact read-only Wi-Fi status card linking to Settings. Polls
    every 5s plus an immediate refresh on `visibilitychange` (mobile
    browsers suspend timers while backgrounded — without this the
    page shows stale data until manually reloaded). Footer with
    copyright/year and live date/time.

    Cards lay out via a small hand-rolled JS masonry (`layoutCards()`
    in `app.js`/`settings.js`), after both pure-CSS options failed
    live: Grid forces uniform row height, stranding shorter cards
    below a tall neighbor with a big gap; CSS multi-column ("masonry")
    packs by shortest-current-column, which reflows *every* card's
    position whenever any one card's height changes, not just cards
    below it. The JS version assigns each card a FIXED column by DOM
    order (`i % columns`) — a card's column never changes due to
    another card's height, only its offset within its own column
    does. `ResizeObserver` on every card triggers relayout
    automatically on any content change; column count itself only
    changes on an actual window-width breakpoint crossing. This
    layout fits the Dashboard's many small cards, but actively hurt
    the Traffic/IP Scanner/Port Scanner pages (numbers wrapping, a
    wide table needing a scrollbar with room to spare) -- those use
    `.stacked-cards` on `<main>` instead (plain full-width flow, no
    JS) via a reusable class, not page-specific IDs, since more such
    pages are planned.
  - **Traffic** (`/traffic.html` + `traffic.js`): passive traffic
    statistics summary and a Top Talkers table, `.stacked-cards`.
  - **IP Scanner** (`/ip-scanner.html` + `ip-scanner.js`): nmap
    ping-sweep with live-streaming results, `.stacked-cards`.
  - **Port Scanner** (`/port-scanner.html` + `port-scanner.js`): nmap
    SYN scan across a port range on one host, range presets,
    `.stacked-cards`.
  - **Modbus** (`/modbus.html` + `modbus.js`): Modbus TCP read form
    (host/port/unit ID/function/address/quantity) and a results
    table, `.stacked-cards`.
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
  `ping.py`.
  **Second bug found and fixed, also user-reported** ("have to spam
  Stop and wait"): mtr forks a per-target `mtr-packet` helper that
  inherits the stdout/stderr pipe. Signalling only the tracked `mtr`
  process left `mtr-packet` orphaned (confirmed live via `ps`:
  reparented to PID 1, still running) and still holding that pipe
  open, so `communicate()` in the reader thread kept blocking for EOF
  that only `mtr-packet` exiting would send -- explaining why repeated
  clicks against an already-dead parent looked like it needed
  flooding, when really the actual holdout was never being signalled
  at all. Fixed with `start_new_session=True` (own process group) +
  `os.killpg` on stop, reaching `mtr` and its children in one shot.
  Confirmed live: stop now takes **~0.4s** (was 1-2s+, sometimes
  longer), and `ps` shows zero leftover processes afterward. Normal
  completion re-verified working after the process-group change too.
- DHCP lease info: **fully confirmed working** -- `lease_time_seconds`,
  `dhcp_server`, and `domain_name` (null when unset, not an empty
  string) all populated correctly from a real DHCP lease on eth0.
- Traffic statistics / top talkers: **fully confirmed working against
  real traffic** -- packet/byte counts, broadcast/multicast/unicast
  split, and per-talker breakdown all came out internally consistent
  (e.g. a talker's `broadcast` count matching its `arp` count for a
  device only seen doing ARP requests, which are broadcast by
  definition). Talkers correctly keyed by IP (ARP/IPv4 sources) or
  fell back to source MAC for L2-only traffic (a MikroTik's MAC showed
  up sending pure multicast, consistent with MNDP's own broadcast
  behavior). `reset()` confirmed to actually zero everything and
  restart accumulation. CPU impact of the unfiltered capture checked
  live: load average stayed at ~0.09/0.05/0.07 (essentially idle) with
  four tcpdump processes now running concurrently (LLDP/CDP/MNDP's
  filtered ones plus this one's unfiltered one) -- but only under the
  current quiet home-LAN traffic level; not re-verified under heavier
  or more realistic (e.g. actual industrial PROFINET/S7) traffic.
  PROFINET/S7 detection itself (EtherType 0x8892 / TCP port 102) is
  implemented but **not yet confirmed against a real device sending
  either** -- no such device on hand, same "implementation verified,
  traffic not observed" situation LLDP/CDP were in before a suitable
  neighbor was available.
- IP scanner: **fully confirmed working** -- real 13-host `/24` scan
  in ~4s with correct IP/MAC/vendor for every host (Proxmox, Apple,
  Raspberry Pi, MikroTik all identified correctly by vendor). Found
  and fixed a real bug along the way: `setcap` (the pattern used for
  tcpdump/arp-scan) got the scan running but silently dropped
  MAC/vendor from every result -- confirmed live that nmap's ARP-based
  MAC discovery specifically requires `geteuid()==0`, not just
  `cap_net_raw`/`cap_net_admin`, by comparing an unprivileged-but-capable
  run against a real-root run on the same single host. Switched to
  running nmap via `sudo` instead. Stop also confirmed: mid-scan
  cancel takes ~0.6s with the same process-group approach as MTR's fix
  (applied here preemptively), zero leftover processes after.
- Port scanner: **fully confirmed working** -- real scan against a
  MikroTik router (1-1024 range, ~5.5s) correctly found ports 22/53/80
  (ssh/domain/http) open, matching what's actually enabled on that
  device. Full-range scan (1-65535) confirmed startable and stoppable
  (~0.9s to cancel, zero leftover processes) -- caught and fixed a
  self-inflicted bug before deploying: the initial port-count cap
  (10000) would have made the page's own "All (1-65535)" preset button
  always fail its own validation; removed the redundant cap since
  1-65535 was already the real bound.
- Modbus TCP read: protocol logic first confirmed against a local fake
  Modbus TCP server (correct holding-register value decoding, correct
  bit-packed coil decoding, connection refused handled gracefully,
  function-code/quantity validation rejects bad input before any
  request goes out, a genuine Modbus exception response parsed into a
  readable message rather than raw bytes), **then confirmed again
  against a real Modbus TCP slave on the network** (192.168.88.21):
  function code 3 (holding registers) returned real, changing values;
  function code 4 (input registers) correctly came back with a genuine
  "Illegal Function" exception, since that device doesn't support it
  -- both outcomes exactly matched what the code should do. Passive
  mode's "no source IP" error path confirmed live on the real Pi too.
  **What's still unconfirmed**: the Kamstrup-specific device templates
  (register addresses, float32 word order) -- protocol mechanics and
  a specific manufacturer's register-map accuracy are separate claims,
  and the slave tested against wasn't a Kamstrup meter. Two Kamstrup
  templates (types 300/302, 41+52 registers, from the maintainer's own
  documentation) added to `config/modbus_templates.json` (tracked in
  git -- a register map is manufacturer documentation, not site data),
  including float32 register decoding (`decoded_value` in the API) and
  fixed labeling for the sheet's "*" High Resolution register variants.
- Power/undervoltage status added to the System card
  (`system_info.get_power_status()`, via `vcgencmd get_throttled`) --
  **confirmed live on the deployed Pi, and found a real problem**: the
  Pi currently reports `0x50005`, i.e. undervoltage and throttling
  active *right now*, not just a past occurrence. The maintainer's
  power supply/cable needs attention.
- All Dashboard/IP-Scanner/Port-Scanner/Modbus form fields now persist
  last-used values via `localStorage` (user feedback: retyping host/
  target/etc for every run got old); Ping and MTR's host fields also
  got a real default value (`8.8.8.8`) instead of a placeholder-only
  hint that looked filled in but wasn't (submitting with nothing typed
  silently no-op'd on the `if (!host) return` guard).
- v0.2.4 Modbus TCP diagnostics expansion -- **every new piece
  confirmed live against the real Modbus TCP slave at 192.168.88.21**:
  - `read()`'s new `response_time_ms`/`raw_request`/`raw_response`
    fields: confirmed on a real holding-register read (real timing,
    real hex bytes matching the actual request/response).
  - Device Identification (FC43/MEI14): confirmed the real slave
    returns "Illegal Function" (it doesn't support this), and that
    this is correctly reported as `supported: false` with a clear
    message, not shown as a generic communication error.
  - Data interpretation helper: confirmed against real register values
    read from the slave (UINT16/INT16/HEX/BINARY/UINT32/INT32/FLOAT32
    all populated correctly for a real 2-register pair).
  - Unit ID scan (range 1-5): confirmed live, all 5 unit IDs classified
    "responding" (this particular test slave answers any unit ID).
  - Register range scan (holding registers, 0-19): confirmed live --
    single-block probe succeeded (all 20 readable), no unnecessary
    bisection, `progress`/`total` matched exactly (20/20).
  - Live polling (500ms interval): confirmed live, 5/5 successful
    requests, 0 timeouts/exceptions, real min/avg/max response times
    (28.4/33.3/40.5 ms).
  - Passive Modbus TCP traffic analysis: **this is the strongest
    verification of the batch** -- after running the reads/scans/poll
    above, `/api/tools/modbus/traffic` showed every one of them
    correctly captured and correlated purely passively through the
    shared dispatcher: 8 requests/8 responses on the main FC3
    relationship (matching the sum of every FC3 read made), 2
    requests/2 responses/**2 exceptions** on the FC43 relationship
    (matching the 2 Device Identification attempts, both correctly
    getting the real "Illegal Function" exception), and unit IDs 2-5
    each showing exactly 1 request/response (matching the unit scan
    exactly) -- real proof the Transaction-ID-based request/response
    correlation works correctly against genuine traffic, not just the
    synthetic frames in `test_modbus_traffic.py`.
  - Frontend (Read/Scan/Monitor/Traffic tabs): static assets confirmed
    serving correctly (200s, all tab container IDs present in the
    served HTML) and all `getElementById` references in `modbus.js`
    cross-checked against `modbus.html`'s actual element IDs with no
    mismatches. **Not independently tested in a real browser** --
    no browser/display available in this environment; the interactive
    behavior (tab switching, form submission, live-updating tables)
    is inferred from code review and the confirmed-correct API
    responses those handlers consume, not from clicking through it.
  - Device Registry integration (brief item #10) explicitly not
    implemented -- its dependency doesn't exist (see Known gaps and
    README's Roadmap).
- Link event history (2026-08-22): baseline snapshot on deploy came
  back correctly as a single initial event (100 Mbps/full/UP against
  the connected test device), stayed stable with no spurious entries
  over ~8s of steady traffic, then a real cable pull/replug on the
  physical eth0 port produced exactly the expected pair of events --
  DOWN (`operstate: DOWN`, `link_detected: false`, `speed_mbps`/
  `duplex` both `null`) the moment the cable came out, UP with the
  correct restored `speed_mbps: 100`/`duplex: full` on replug -- both
  observed live via repeated polling of `/api/network/eth0/history`
  during the pull/replug, not just asserted from the unit tests.
- Duplicate-IP and rogue-DHCP detection (2026-08-22):
  - DHCP: cycling eth0 Passive→DHCP via the API forced a real DHCP
    transaction against the real gateway. `/api/network/dhcp-servers`
    correctly captured it: `server_ip: 192.168.88.1`, the gateway's
    real MAC, `acks: 1`, `offered_ip_sample: ["192.168.88.147"]` --
    matching exactly what `/api/network/eth0/mode` reported as the
    freshly-leased address. `offers: 0` because the client did a
    direct DHCPREQUEST/ACK renewal (it already held this lease from
    before) rather than a full DISCOVER/OFFER cycle -- correct
    behavior for that client state, not a parsing gap.
  - IP conflicts: `/api/network/ip-conflicts` correctly flagged a real
    conflict on `192.168.88.149`, claimed by two real MAC addresses
    seen on the wire. Investigating which devices they were revealed
    both belong to the Pi itself (`b8:27:eb:9a:d3:eb` is eth0's own
    MAC from `/api/network/eth0`, the other is wlan0's) -- an artifact
    of this development rig's eth0 TEST PORT currently being plugged
    into the same LAN segment as wlan0's network rather than a
    genuinely isolated test network, not a detector bug: two real MAC
    addresses really were claiming the same real IP on the wire the
    capture sees, exactly the condition this feature is meant to
    surface. Confirms the detection logic works end-to-end against
    real ARP traffic, not just the synthetic frames in
    `test_ip_conflict.py`.

## Known gaps

- PROFINET/S7 traffic detection implemented but not yet confirmed
  against a real device (see Verified section above).
- Modbus TCP read client's protocol logic is confirmed against a real
  Modbus slave (see Verified section above), but the Kamstrup device
  templates' actual register map is not -- PROFINET DCP/traffic
  detection, S7 diagnostics, and industrial device ID (rest of V0.3)
  not started.
- No authentication on the web UI or API -- **deliberate, not an
  oversight**: the maintainer's call (2026-08-17) is that this stays
  a deliberately primitive field tool (LAN-only, no auth), not a
  hardened multi-user product. Revisit only if the threat model
  actually changes (e.g. LanPi starts getting exposed somewhere less
  trusted than a LAN/fallback-AP client).
- Test coverage (`tests/`) is parser/classifier/validation-level only
  -- no integration tests against the real `tcpdump`/`nmap`/`mtr`/
  `nmcli` binaries themselves (`test_pcap.py`'s rotation/pruning tests
  use a process-spawn stand-in, not real tcpdump; `dispatcher.py`'s
  real capture loop isn't exercised in pytest at all, deliberately --
  see `test_dispatcher.py`'s docstring for why), the actual
  background-thread listeners, or a live FastAPI app with startup
  events firing. Those still rely on the manual
  live-verification-on-the-Pi discipline described throughout this
  file.
- The fallback AP's activate/deactivate path (`ap.py`, `shell.
  run_privileged()`) wasn't independently live-tested in the v0.2.3
  #10 migration -- structurally identical to the already-verified
  wifi.py/eth0_mode.py usage, but not flipped live to confirm (see
  Verified section above).
- Modbus passive traffic analysis (`modbus_traffic.py`) does not do
  TCP stream reassembly -- a request/response split across TCP
  segments won't be parsed (silently undercounted, not misread), and
  "missing response" is a best-effort signal, not proof (a response
  this capture simply missed looks identical to one that was never
  sent). Documented in the module and surfaced in the UI; real traffic
  in this session's live test was well within one packet each way, so
  this specific edge case hasn't been hit/tested.
- The v0.2.4 Modbus frontend (Read/Scan/Monitor/Traffic tabs) was not
  tested in a real browser -- no display/browser available in this
  environment. Verified: static assets serve correctly, every
  `modbus.js` element reference matches a real `modbus.html` id, and
  every API endpoint the JS calls is independently confirmed correct
  (see Verified section above). Not verified: actual click-through
  behavior, tab switching, or visual layout.
- The new "Link Event History" dashboard card (`index.html`/`app.js`)
  has the same gap as above: the API it renders was live-verified
  directly (see Verified section above, including a real cable pull/
  replug), and every `getElementById` reference in the new JS matches
  a real element id, but the actual rendered table/reset button was
  not clicked through in a real browser.
- The new "IP Conflict Detection" and "DHCP Server Detection" dashboard
  cards have the same gap: both APIs were live-verified directly
  against real ARP/DHCP traffic (see Verified section above), and
  every `getElementById` reference in the new JS matches a real
  element id, but neither card was clicked through in a real browser.

## Next steps

- **V0.2.3 Foundation is complete. v0.2.4 Modbus expansion is complete.
  Link event history, duplicate-IP detection, and rogue-DHCP detection
  (V0.3 backlog items) are all complete.** Next up is either V0.3
  industrial protocol work (PROFINET/S7 -- deliberately deferred until
  real PLC hardware is available) or the one remaining V0.3 backlog
  item (unified device registry -- see README), whichever the
  maintainer prioritizes.
- Try the new Modbus tabs and the three new dashboard cards (Link
  Event History, IP Conflict Detection, DHCP Server Detection) in a
  real browser against a real device -- not yet done (see Known gaps
  above).
- Verify the Kamstrup device templates' actual register map against a
  real Kamstrup meter when one's available (the read protocol itself
  is already confirmed against a real Modbus slave).
- Re-verify PROFINET/S7 detection against a real device when one's
  available.
- Re-verify the captive-portal auto-open flow with a phone.
