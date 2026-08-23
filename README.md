# LanPI

**LanPi** is a portable Raspberry Pi-based Ethernet network analysis and diagnostic tool.

The goal of the project is to provide a simple web-based interface for network troubleshooting without requiring a dedicated display or keyboard.

LanPi uses Wi-Fi for management while keeping the physical Ethernet interface dedicated to the network being tested.

It's a DIY, open-source alternative to commercial handheld LAN testers such as Fluke Networks' LinkRunner/LANmeter line, NetAlly's tools, and similar -- link status, neighbor discovery (LLDP/CDP/MNDP), ping/MTR, TCP port and range scanning, and packet capture, on inexpensive hardware you can build and modify yourself. It does not replace certified cable-test/TDR equipment (see the Cable Diagnostics section below) or those tools' compliance-grade certification reports -- it's aimed at everyday field diagnostics, not cable certification.

## Project Goals

LanPi is intended for field network diagnostics, including standard Ethernet and industrial automation networks.

The main design principles are:

* Web-based interface accessible from a phone or laptop
* No dedicated display or keyboard required
* Wi-Fi used exclusively for management
* Ethernet interface dedicated to network testing
* Passive analysis possible without assigning an IP address
* Quick switching between DHCP, static IP and passive modes
* Network discovery using LLDP, CDP and other protocols
* Packet capture for further analysis with Wireshark
* Support for industrial Ethernet diagnostics

## Hardware

Actual development/test platform (confirmed via `GET /api/system` on
the deployed unit):

* Raspberry Pi 3 Model B (Rev 1.2)
* 1 GB RAM
* 10/100 Mbps Ethernet (not Gigabit — `eth0` link tests negotiate at
  100 Mbps)
* Built-in Wi-Fi
* Raspberry Pi OS Lite (Debian trixie)

A Raspberry Pi 4 was the originally planned platform (faster CPU,
Gigabit Ethernet); the project has so far only been built and tested
against the Pi 3 above. Nothing in the code is Pi-4-specific, but
performance under load (e.g. packet capture) hasn't been validated on
either.

## Network Architecture

```text
Phone / Laptop
      │
    Wi-Fi
      │
    wlan0
      │
┌───────────────┐
│     LanPi     │
│ Raspberry Pi  │
└───────────────┘
      │
    eth0
      │
      ▼
 Network under test
```

### wlan0

The Wi-Fi interface is used for management.

During normal operation LanPi connects to a configured Wi-Fi network as a client.

When no known Wi-Fi network is available, LanPi can provide a fallback access point.

Fallback configuration:

```text
SSID: LanPi
IP:   172.24.58.1
```

The web interface and SSH are accessible through the management interface.

### eth0

The Ethernet interface is dedicated exclusively to the network under test.

Supported operating modes:

* Passive / no IP address (the default)
* DHCP client
* Static IP configuration

The management and test networks must remain isolated. LanPi will not bridge `wlan0` and `eth0`.

LanPi's own management interface (port 8000, the web UI/API) is
firewalled off `eth0` via `nftables` -- a device connected to the port
under test cannot reach LanPi's own dashboard through it. SSH (22) is
a deliberate exception, left open on `eth0` as a recovery path if
`wlan0` becomes unreachable (see `ARCHITECTURE.MD` Rule 7).

## Features

What LanPi actually does today. Everything below is implemented and
live-verified against real hardware unless marked otherwise (see
`STATUS.md` for the specifics of each verification -- this section
only describes current behavior, not how it got here). Each entry
notes what it's actually built on, since "what tool/library does X"
was a recurring question worth answering once here rather than
per-feature.

### Ethernet Port Status

Link status, speed, duplex, auto-negotiation, MAC address, MTU, and
RX/TX/error interface counters for `eth0`, via `ip -j -s link` and
`ethtool` (shelled out to, parsed from their normal CLI output --
no netlink library).

### Link Event History

Rather than only the current live snapshot above, a background poller
checks `eth0`'s link state every 2 seconds and appends an event
whenever presence, up/down state, speed, or duplex actually changes --
RX/TX byte counters, which change on nearly every poll by definition,
are deliberately excluded so ordinary traffic never adds a noise
entry. Bounded to the most recent 500 events; resets when the backend
restarts. Live-verified against a real cable pull/replug (see
`STATUS.md`).

### Cable Diagnostics (not supported on either development board)

TDR (Time Domain Reflectometry) wire-pair quality and cable-length
estimation, via `ethtool --cable-test`, when the Ethernet PHY supports
it. **Checked against both a Pi 3 and a Pi 4, neither supports it**:

* Pi 3: `ethtool --cable-test eth0` returns "PHY driver does not
  support cable testing". Its Ethernet is a USB-attached `smsc95xx`
  adapter, not a native PHY with TDR circuitry, so there's no path to
  this feature on this board at all.
* Pi 4 (confirmed 2026-08-22): has a real Gigabit PHY (Broadcom
  BCM54213PE, driver `bcmgenet`, RGMII-attached -- not a USB adapter),
  so it was a genuinely plausible candidate. Still fails the same way:
  `ethtool --cable-test eth0` returns the identical "PHY driver does
  not support cable testing" error. The chip itself may well have TDR
  circuitry, but the Linux kernel's PHY driver for it doesn't
  implement the `cable_test_start`/`cable_test_get_status` callbacks
  ethtool's cable-test command needs -- a kernel/driver-level gap, not
  a hardware one.

Not planned for any version unless a board with actual kernel-level
cable-test support turns up.

### IP Configuration (`eth0`)

Passive (no IP, no L2 traffic, the default), DHCP client, and static
IPv4 (address/gateway/DNS) modes, via `nmcli`. DHCP mode also surfaces
lease details (server, lease time, domain) parsed from
`nmcli`'s `DHCP4.OPTION` output. `eth0` is set to never install a
default route (`ipv4.never-default`) regardless of mode, so the test
port can never hijack the Pi's own outbound traffic away from `wlan0`
(see `ARCHITECTURE.MD` Rule 3).

### Network Health Detection

Two passive listeners on the shared capture dispatcher, both scoped to
the TEST PORT and both reporting raw observations rather than a
verdict, since there's no device registry yet to hold a "known good"
baseline to compare against:

* **Duplicate IP detection** -- tracks every MAC address claiming each
  IP via observed ARP traffic (requests and replies both count as a
  claim); an IP claimed by more than one MAC at once is flagged as a
  conflict. A claim expires after 10 minutes of silence, so a
  genuinely resolved reassignment (a device going offline, a lease
  changing hands) clears itself instead of leaving a phantom conflict
  behind forever.
* **Rogue/unexpected DHCP server detection** -- tracks every distinct
  DHCP server seen answering OFFER/ACK on the segment (keyed by the
  DHCP server-identifier option). `multiple_servers_detected` flips
  true the moment a second one appears -- more than one DHCP server
  answering on the same segment is the actual symptom (clients getting
  leases from whichever server answers first), regardless of which one
  is "correct"; LanPi reports what it sees and leaves the judgment
  call to the operator.

### Network Discovery

* **LLDP / CDP / MNDP** -- hand-rolled parsers (no `scapy` or any
  packet-parsing library), decoding the relevant TLV structure
  directly with Python's `struct` module: LLDP's plain EtherType
  framing, CDP's 802.3 + LLC/SNAP framing, and MNDP's UDP broadcast
  (port 5678) framing. All three confirmed against real neighbors (a
  MikroTik router sends LLDP, CDP, and MNDP all three).
* **ARP scan** -- via the `arp-scan` CLI tool, `--localnet` or an
  explicit network.
* **IP scanner** -- via `nmap -sn` (ping-sweep host discovery,
  combining ICMP/ARP/TCP probes), an explicit CIDR/range rather than
  only "the local subnet".

Future industrial discovery: PROFINET DCP, Siemens device
identification (not started).

### Diagnostic Tools

* **Ping** -- via the system `ping` binary, run as a background
  process so it can be stopped early (SIGINT, so it still prints its
  summary line) instead of blocking for a fixed count. Shows live
  min/avg/max RTT rather than a per-reply list.
* **MTR / Traceroute** -- via the `mtr` CLI tool's `--report --json`
  output (parsed as JSON, not scraped from its text table).
  Background start/stop so a run against an unreachable host can be
  cancelled rather than blocking until it times out on its own.
* **TCP port test** -- hand-rolled (Python's `socket` module
  directly, no library), a single connect probe against one host:port,
  classified as open/closed/timeout.
* **Port scanner** -- via `nmap -sS` (SYN scan) across a port range on
  one host, distinct from the single-port TCP test above.
* **Protocol port presets** on the TCP port test:

  | Protocol   | TCP Port |
  | ---------- | -------: |
  | Siemens S7 |      102 |
  | Modbus TCP |      502 |
  | HTTP       |       80 |
  | HTTPS      |      443 |
  | VNC        |     5900 |

Every active tool above (Ping, TCP test, MTR, IP scanner, port
scanner, Modbus) sources its traffic specifically from `eth0`'s own
address (socket bind, or `ping -I`/`nmap -e`/`mtr -a`), not just any
outbound socket, so it always tests through the TEST PORT rather than
silently going out `wlan0`. Reaching a target beyond `eth0`'s own
directly-connected subnet -- a device behind its own gateway on the
test network, e.g. a Siemens S7 PLC on a routed segment -- works too:
`eth0` gets a real default route via its own gateway when connected,
deprioritized below `wlan0`'s so the Pi's own general traffic
(SSH/git/updates/...) still prefers `wlan0` whenever it has any route
at all (see `ARCHITECTURE.MD` Rule 3). Confirmed working for Ping, MTR,
and TCP test alike against a real out-of-subnet target.

### Modbus TCP

A dedicated Modbus page (Read / Scan / Monitor / Traffic tabs), built
on a hand-rolled Modbus TCP client -- **not `pymodbus` or any Modbus
library**, the MBAP header + PDU format is simple enough to build and
parse directly with `socket` + `struct`, consistent with this
project's LLDP/CDP/MNDP parsers. Read-only by design, no write
functions (see Safety, below).

**Read:**

* Coils, discrete inputs, holding registers, input registers (function
  codes 1-4), with response time and an optional raw request/response
  hex view on every read
* **Device Identification** (FC43 / MEI type 14) -- vendor/product/
  model/revision info, when the device supports it; a device that
  doesn't is reported distinctly ("not supported"), not shown as a
  communication failure
* **Data interpretation** on the last read's register values --
  UINT16/INT16/UINT32/INT32/FLOAT32/HEX/Binary, with an explicit,
  caller-chosen byte order (ABCD/BADC/CDAB/DCBA) rather than a guess
* Named **device templates** -- a unit ID plus a labeled list of
  registers (including 32-bit float decoding), read all at once with
  their labels attached, so a known device type's function code/
  address/quantity don't need re-entering by hand every time. Templates
  live in `config/modbus_templates.json` (tracked in git -- a device's
  register map is manufacturer documentation, not site-specific data);
  `config/modbus_templates.example.json` is a placeholder showing the
  format.

**Scan:**

* **Unit ID scan** -- probes a range of unit IDs (useful behind a
  Modbus TCP-to-RTU gateway), sequential and conservative; a Modbus
  exception still counts as "responding"
* **Register range scan** -- finds which addresses are readable with
  no register map to go on, probing whole blocks at once and only
  bisecting where a block fails, so a mostly-readable or
  mostly-unreadable range costs O(log n) requests, not one per register

**Monitor:**

* **Live polling** -- repeatedly reads one register/block on an
  interval, tracking request/timeout/exception counts and
  response-time min/avg/max, useful for spotting an unstable or
  overloaded device a single read wouldn't reveal

**Traffic:**

* **Passive Modbus TCP analysis** -- built on the same shared capture
  dispatcher as LLDP/CDP/MNDP/Traffic Stats (no separate listener),
  tracking client/server/unit-ID/function-code relationships with
  request/response/exception counts and timing, correlated via the
  Modbus TCP Transaction ID. Inspects individual captured packets
  without TCP stream reassembly, so a request/response split across
  TCP segments won't be counted, and "missing response" is a
  best-effort signal, not proof -- a response this capture simply
  missed looks identical to one that was never sent.

### Passive Traffic Analysis

Own dedicated Traffic page: live packet/byte counters, broadcast/
multicast/unicast split, and a **Top Talkers** table, via hand-rolled
classification of each frame's Ethernet/IPv4/ARP headers plus UDP/TCP
ports (no deep payload inspection):

* Per-protocol counts: ARP, IPv4, IPv6, DHCP, LLDP, CDP, mDNS, SSDP,
  PROFINET (EtherType `0x8892`), S7 (TCP port 102)
* Top Talkers grouped by source MAC (always present at L2, unlike IP)
  with the most recently seen source IP attached to the same entry --
  a device sending both IPv4 and LLDP/CDP/PROFINET traffic is one row,
  not two. Ranked by cumulative bytes over the summary period (since
  start or last Reset); every column is sortable.
* LanPi's own `eth0` traffic (from its own diagnostic tools) is
  excluded from the Top Talkers table specifically, but still counted
  in the overall totals.

Future industrial protocol analysis: PROFINET and S7 traffic detection
exist as basic EtherType/port matches in the classifier above but
aren't yet confirmed against real devices sending either (no such
device available to test against yet).

### Packet Capture

Start/stop packet capture on `eth0` to a `.pcap` file, via `tcpdump`
(no BPF filter by default, or an optional custom BPF filter),
downloadable and directly openable in Wireshark. Runs as a background
process (mirrors Ping's design) with an optional fixed duration or
manual stop.

Storage is self-managing: a long-running capture automatically splits
into a new file every ~100MB (clean stop/restart, not `tcpdump`'s own
`-C`, which breaks the `.pcap` extension), and saved captures are
pruned oldest-first once total storage passes ~1GB -- a capture left
running unattended can't silently fill the SD card.

## Software Architecture

What LanPi is actually built on (as opposed to originally planned --
see git history / `STATUS.md` for how a couple of these choices
changed along the way):

* **Backend**: Python, FastAPI, uvicorn -- no ORM, no database,
  everything is either read live from `/proc`/`/sys`/CLI tools or held
  in memory. Routes are split by feature under `backend/api/routes/`
  rather than one large file.
* **Network configuration**: NetworkManager, exclusively via `nmcli`
  (no `dhcpcd`-based Pi OS releases supported).
* **Neighbor discovery** (LLDP/CDP/MNDP), **traffic statistics/Top
  Talkers**, and **passive Modbus TCP analysis**: hand-rolled parsers,
  no `scapy` or any packet-parsing library anywhere in the project. All
  five share one background `tcpdump -w -` capture
  (`backend/capture/dispatcher.py`) instead of running one process
  each; each listener filters and parses the frames it cares about
  from that shared feed with `struct`. The dispatcher's own health (is
  it actually running, when did it last see a packet) is exposed in
  `/api/status`.
* **Modbus TCP**: hand-rolled client (`socket` + `struct`) -- no
  `pymodbus` or any Modbus library. Unit ID scan, register range scan,
  and live polling are plain Python background threads over that same
  client (no subprocess involved, so no process-lifecycle concerns
  the way `mtr`/`nmap` have).
* **TCP port test**: hand-rolled (`socket`), no library.
* **Packet capture**: `tcpdump`, writing real `.pcap` files.
* **Shelling out to system binaries** (`tcpdump`, `nmap`, `mtr`,
  `nmcli`, `ethtool`, ...): consistent binary discovery and
  `subprocess.run` wrapping via `backend/shell.py`, rather than each
  module rolling its own.
* **IP scanning**: `nmap -sn`.
* **Port scanning**: `nmap -sS` (needs real root -- run via `sudo`,
  not `setcap`, see `STATUS.md` for why `setcap` alone silently drops
  MAC/vendor data from `nmap`'s output).
* **MTR / traceroute**: `mtr --report --json`.
* **ARP scan**: `arp-scan`.
* **Ping**: the system `ping` binary.
* **Link status**: `ip -j -s link`, `ethtool`.
* **Fallback access point**: `hostapd` + `dnsmasq` (not
  NetworkManager's built-in hotspot mode -- see `STATUS.md` for why).
* **Frontend**: vanilla HTML/CSS/JavaScript, no framework, no build
  step, no bundler. Each page (Dashboard, Traffic, IP Scanner, Port
  Scanner, Modbus, Settings) is a standalone `.html` + `.js` pair, plus
  one shared `active-tasks.js` included on every page: a small pulsing
  badge next to the page title naming any background job (Ping, MTR,
  Capture, IP scan, port scan, or one of the three Modbus background
  tasks) that's still running, even if it was started from a different
  page or the page has since been reloaded -- these jobs run on the Pi
  independently of any particular page, so this is the one place
  that's always visible showing what's still active.

## Development Approach

LanPi is an experimental **vibe-coded / AI-assisted project**.

A significant portion of the project is developed with the assistance of AI coding tools, while the overall architecture, requirements, testing, and real-world network validation are performed by the project maintainer.

AI-generated code should not be assumed to be correct or secure by default. Network configuration, packet handling, protocol implementations, and other potentially disruptive functionality should be reviewed and tested before use on production networks.

The project is also intended as a practical experiment in building a useful network engineering tool through AI-assisted development.

## Repository Structure

```text
lanpi/
├── README.md
├── ARCHITECTURE.MD
├── STATUS.md
├── LICENSE
├── .gitignore
├── VERSION                      # single source of truth, read by backend/version.py
├── requirements.txt
├── requirements-dev.txt         # + pytest/httpx, for running tests/
├── pytest.ini
│
├── tests/                       # pytest -- parsers/classifiers/validation,
│   │                             # no real hardware needed (see Running Tests)
│   ├── test_lldp.py / test_cdp.py / test_mndp.py
│   ├── test_traffic_stats.py
│   ├── test_modbus.py / test_modbus_templates.py
│   ├── test_modbus_decode.py / test_modbus_unit_scan.py
│   ├── test_modbus_register_scan.py / test_modbus_poll.py / test_modbus_traffic.py
│   ├── test_eth0_mode.py
│   ├── test_ip_scanner.py / test_port_scanner.py
│   ├── test_pcap.py / test_dispatcher.py / test_shell.py
│   └── test_api.py
│
├── backend/
│   ├── main.py                 # FastAPI app, static file serving, startup listeners
│   ├── shell.py                 # shared binary-discovery/subprocess helper
│   ├── api/routes/             # every /api/* endpoint, split per feature (health,
│   │                           # system, network, discovery, tools, modbus,
│   │                           # capture, traffic)
│   ├── network/                # wifi, eth0 mode, ap, link status
│   ├── discovery/               # lldp.py, cdp.py, mndp.py (passive, background)
│   ├── tools/                  # ping, arp_scan, tcp_test, mtr, ip_scanner,
│   │                           # port_scanner, system_info, modbus.py,
│   │                           # modbus_templates.py, modbus_decode.py,
│   │                           # modbus_unit_scan.py, modbus_register_scan.py,
│   │                           # modbus_poll.py
│   └── capture/                # dispatcher.py (shared tcpdump feeding every
│                                # passive listener), pcap.py, traffic_stats.py,
│                                # modbus_traffic.py
│
├── frontend/                   # one .html + .js pair per page, no build step
│   ├── index.html / app.js     # Dashboard
│   ├── traffic.html / traffic.js
│   ├── ip-scanner.html / ip-scanner.js
│   ├── port-scanner.html / port-scanner.js
│   ├── modbus.html / modbus.js
│   ├── settings.html / settings.js
│   └── style.css               # shared by every page
│
├── config/
│   ├── modbus_templates.json           # real device register maps (tracked in git)
│   └── modbus_templates.example.json   # placeholder, shows the format
│
├── system/
│   ├── lanpi.service, lanpi-wifi-fallback.service
│   ├── install.sh
│   ├── lanpi-ap-up.sh / lanpi-ap-down.sh
│   ├── hostapd.conf.template, dnsmasq-ap.conf, 99-lanpi-no-forward.conf
│   └── nftables.conf            # Rule 7: blocks port 8000 on eth0, SSH stays open
│
└── docs/
```

## Installation

Tested on: Raspberry Pi OS (Debian **trixie**, aarch64), system Python
**3.13**, FastAPI **0.141**, uvicorn **0.52** (see `requirements.txt`
for the unpinned dependency list — these are the versions actually
verified against real hardware, not hard requirements). Any Raspberry
Pi OS release with NetworkManager as the network backend should work;
older releases using `dhcpcd` instead of NetworkManager are not
supported, since Wi-Fi/eth0 management goes through `nmcli`.

1. Flash Raspberry Pi OS (Lite is enough) and enable SSH, either via
   Raspberry Pi Imager's advanced options or by running
   `sudo raspi-config` after first boot.

2. SSH into the Pi and clone this repository (it's private, so make
   sure you're authenticated -- e.g. `gh auth login`, or a personal
   access token when prompted for a password):

   ```bash
   git clone https://github.com/SpenioPantollor/LanPI.git ~/lanpi
   cd ~/lanpi
   ```

3. Run the installer:

   ```bash
   bash system/install.sh
   ```

   This provisions a Python venv, installs system packages
   (`tcpdump`, `ethtool`, `hostapd`, `dnsmasq`), grants `tcpdump`
   unprivileged packet-capture capability, sets up the fallback
   access point (prints its SSID/password **once**, on first install
   only — write it down), disables auto-DHCP on `eth0` so it starts
   passive by default, and installs/enables the systemd services.

4. Open `http://<pi-ip>:8000/` from another device on the same
   network (or `http://172.24.58.1:8000/` if connected to LanPi's own
   `LanPi` fallback Wi-Fi).

**Port: `8000`, plain HTTP (no TLS).** This is fixed — `lanpi.service`
always binds `0.0.0.0:8000` (see `system/lanpi.service`). The IP is
whatever `<pi-ip>` resolves to on your network (`lanpi.local`, or
check your router/`ip -4 addr show wlan0` on the Pi) except for the
fallback AP, which is always `172.24.58.1` (see the Network
Architecture section above).

To update an existing install: `git pull` then
`sudo systemctl restart lanpi.service`. Re-running
`system/install.sh` is safe any time — it's idempotent and won't
regenerate the fallback AP password or touch an already-configured
`hostapd.conf`.

## Running Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Covers the hand-rolled LLDP/CDP/MNDP parsers, the traffic classifier
(including the talker-merge and self-MAC-exclusion logic), the Modbus
TCP client (against a local fake server, not real hardware), Modbus
device templates, `eth0` mode parsing/validation, and API-layer request
validation -- all pure logic, so none of it needs a real Pi, `eth0`
interface, or network device to run. It does need a Python version
FastAPI's pinned release actually supports (see Installation above) --
notably *not* the 3.9 shipped with older macOS, which is a real gap
for local development on such a machine, not a limitation of the tests
themselves.

The same suite runs automatically on every push to `main` and every
pull request via GitHub Actions (`.github/workflows/tests.yml`).

## Roadmap

Not implemented yet. See `STATUS.md` for day-to-day progress and
`ARCHITECTURE.MD` for the longer-term plan and past decisions behind
these.

**Industrial protocols** (deliberately deferred until real hardware is
available to test against):

* PROFINET DCP discovery and traffic detection
* Siemens S7 diagnostics
* Industrial device identification
* BACnet (building automation) -- discovery/traffic detection, needs a
  real BACnet device (BMS controller, field device) to test against
* MQTT -- passive broker/topic detection at minimum, possibly a
  read-only subscribe/inspect tool later; increasingly common for
  industrial telemetry alongside the more classical fieldbus protocols
  above
* EtherNet/IP (CIP) -- Rockwell/Allen-Bradley's industrial protocol,
  needs real PLC hardware like PROFINET/S7 above
* EtherCAT -- real-time fieldbus, needs a real EtherCAT master/slave
  setup to test against; likely detection-only (frame/topology
  observation) rather than a full master implementation, given this
  project's read-only/non-disruptive stance elsewhere

**General diagnostics:**

* Unified device registry -- a single list merging every
  passively-observed device (LLDP/CDP/MNDP neighbors, Top Talkers,
  Modbus traffic, scan results) into one view. Overlaps with a
  "passive device discovery" feature already considered and dropped
  once as redundant with Traffic's Top Talkers table (see
  `STATUS.md`) -- worth a deliberate decision when the time comes, not
  assumed. Modbus Device Identification and passive Modbus traffic
  observations are both natural inputs to this once it exists, but
  aren't wired into anything today since the registry itself doesn't
  exist yet.

## Safety

LanPi should default to passive operation whenever possible.

Functions capable of modifying network devices, including PROFINET DCP configuration commands, should not be enabled by default.

The management Wi-Fi interface should be protected from configuration changes performed by Ethernet diagnostic functions.

## Related Projects

LanPi is inspired in part by existing Raspberry Pi network diagnostic projects such as PiScout / RaspberryFluke and NetPi.

Where third-party source code is reused, the original copyright notices and license requirements must be preserved.

## License

LanPi is released under the MIT License.
