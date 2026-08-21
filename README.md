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

The web interface and SSH will be accessible through the management interface.

### eth0

The Ethernet interface is dedicated exclusively to the network under test.

Supported operating modes are planned to include:

* Passive / no IP address
* DHCP client
* Static IP configuration

The management and test networks must remain isolated. LanPi will not bridge `wlan0` and `eth0`.

## Features

Everything below is implemented and live-verified against real
hardware unless marked otherwise (see `STATUS.md` for the specifics
of each verification, and the Development Roadmap section below for
the checklist this is derived from). Each entry notes what it's
actually built on, since "what tool/library does X" was a recurring
question worth answering once here rather than per-feature.

### Ethernet Port Status

Link status, speed, duplex, auto-negotiation, MAC address, MTU, and
RX/TX/error interface counters for `eth0`, via `ip -j -s link` and
`ethtool` (shelled out to, parsed from their normal CLI output --
no netlink library).

### Cable Diagnostics (not supported on the current development hardware)

TDR (Time Domain Reflectometry) wire-pair quality and cable-length
estimation, via `ethtool --cable-test`, when the Ethernet PHY supports
it. **Checked against the actual Pi 3 in use: not supported** --
`ethtool --cable-test eth0` returns "PHY driver does not support
cable testing". The Pi 3's Ethernet is a USB-attached `smsc95xx`
adapter, not a native PHY with TDR circuitry, so there's no path to
this feature on this specific board.

Raspberry Pi 4 is a more plausible candidate -- it has a real Gigabit
PHY (Broadcom BCM54213PE) instead of a USB-attached adapter -- but
that alone doesn't confirm cable-test support: the Linux kernel's PHY
driver for that specific chip also has to implement the
`cable_test_start`/`cable_test_get_status` callbacks ethtool's
cable-test command relies on, and that hasn't been checked against
real Pi 4 hardware. Don't assume it works there either without
actually running `ethtool --cable-test eth0` on one. Not planned for
any version until confirmed on hardware that's actually been tested.

### IP Configuration (`eth0`)

Passive (no IP, no L2 traffic, the default), DHCP client, and static
IPv4 (address/gateway/DNS) modes, via `nmcli`. DHCP mode also surfaces
lease details (server, lease time, domain) parsed from
`nmcli`'s `DHCP4.OPTION` output. `eth0` is set to never install a
default route (`ipv4.never-default`) regardless of mode, so the test
port can never hijack the Pi's own outbound traffic away from `wlan0`
(see `ARCHITECTURE.MD` Rule 3).

### Network Discovery

* **LLDP / CDP / MNDP** -- hand-rolled parsers (no `scapy` or any
  packet-parsing library), each a background thread streaming raw
  frames from `tcpdump -w -`'s pcap output and decoding the relevant
  TLV structure directly with Python's `struct` module: LLDP's plain
  EtherType framing, CDP's 802.3 + LLC/SNAP framing, and MNDP's UDP
  broadcast (port 5678) framing. All three confirmed against real
  neighbors (a MikroTik router sends LLDP, CDP, and MNDP all three).
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

Every active tool above (TCP test, MTR, IP scanner, port scanner,
Modbus) sources its traffic specifically from `eth0`'s own address
(socket bind, or `nmap -e`/`mtr -a`), not just any outbound socket --
`eth0` deliberately has no default route, so an unbound connection to
a host outside `eth0`'s subnet would otherwise silently go out
`wlan0` instead.

### Modbus TCP

Read-only Modbus TCP client -- coils, discrete inputs, holding
registers, input registers (function codes 1-4). **Hand-rolled, not
`pymodbus` or any Modbus library**: the MBAP header + PDU format is
simple enough to build and parse directly with `socket` + `struct`,
consistent with this project's LLDP/CDP/MNDP parsers. No write
functions by design (see Safety, below).

Includes 32-bit IEEE float decoding for registers that need it (many
metering devices, e.g. Kamstrup heat/water meters, report everything
as float32 across register pairs) and named **device templates** --
a unit ID plus a labeled list of registers, read all at once with
their labels attached, so a known device type's function code/
address/quantity don't need re-entering by hand every time. Templates
live in `config/modbus_templates.json` (tracked in git -- a device's
register map is manufacturer documentation, not site-specific data);
`config/modbus_templates.example.json` is a placeholder showing the
format.

### Passive Traffic Analysis

Own dedicated Traffic page: live packet/byte counters, broadcast/
multicast/unicast split, and a **Top Talkers** table, built on a
background `tcpdump` capture with **no BPF filter** (unlike the
narrowly-filtered LLDP/CDP/MNDP listeners, this one has to see
everything) and hand-rolled classification of each frame's Ethernet/
IPv4/ARP headers plus UDP/TCP ports (no deep payload inspection):

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

## Software Architecture

What LanPi is actually built on (as opposed to originally planned --
see git history / `STATUS.md` for how a couple of these choices
changed along the way):

* **Backend**: Python, FastAPI, uvicorn -- no ORM, no database,
  everything is either read live from `/proc`/`/sys`/CLI tools or held
  in memory.
* **Network configuration**: NetworkManager, exclusively via `nmcli`
  (no `dhcpcd`-based Pi OS releases supported).
* **Neighbor discovery** (LLDP/CDP/MNDP) and **traffic
  statistics/Top Talkers**: hand-rolled parsers reading raw
  `tcpdump -w -` pcap streams directly with `struct` -- no `scapy` or
  any packet-parsing library anywhere in the project.
* **Modbus TCP**: hand-rolled client (`socket` + `struct`) -- no
  `pymodbus` or any Modbus library.
* **TCP port test**: hand-rolled (`socket`), no library.
* **Packet capture**: `tcpdump`, writing real `.pcap` files.
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
  Scanner, Modbus, Settings) is a standalone `.html` + `.js` pair.

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
├── requirements.txt
│
├── backend/
│   ├── main.py                 # FastAPI app, static file serving, startup listeners
│   ├── api/routes.py           # every /api/* endpoint
│   ├── network/                # wifi, eth0 mode, ap, link status
│   ├── discovery/               # lldp.py, cdp.py, mndp.py (passive, background)
│   ├── tools/                  # ping, arp_scan, tcp_test, mtr, ip_scanner,
│   │                           # port_scanner, modbus, modbus_templates, system_info
│   └── capture/                # pcap.py, traffic_stats.py
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
│   └── hostapd.conf.template, dnsmasq-ap.conf, 99-lanpi-no-forward.conf
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
   sure a deploy key or your own SSH key is set up on the Pi first):

   ```bash
   git clone <this-repo-url> ~/lanpi
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

## Development Roadmap

### Version 0.1

Initial usable network tester. Checked = implemented **and** verified
against the real deployed Pi (see `STATUS.md` for the specifics of
each test):

* [x] Wi-Fi management (client + fallback AP) — incl. a real iPhone
  joining the fallback AP end-to-end
* [x] Web interface — Dashboard + Settings pages
* [x] Ethernet link information
* [x] Passive Ethernet mode
* [x] DHCP mode
* [x] Static IPv4 configuration
* [x] Ping
* [x] LLDP discovery — confirmed with a real neighbor after connecting
  to a managed router
* [x] CDP discovery — **fully confirmed working**, including a real
  neighbor (MikroTik RouterOS turns out to send CDP-compatible
  announcements too, not just its own MNDP — corrected after
  initially assuming otherwise)
* [x] ARP scan — found real hosts (IP/MAC/vendor) on the local network
* [x] TCP port test — confirmed both `open` (a real listening port)
  and `timeout` (an unreachable host) outcomes; sourced specifically
  from eth0's address so it can't silently go out wlan0 instead
* [x] PCAP capture — confirmed start/stop (duration-based and manual),
  BPF filtering, list, download (valid .pcap output), delete, and
  input validation (invalid filter, path traversal on filenames)

### Version 0.2

Extended network diagnostics:

* [x] IP scanner (subnet host discovery) -- confirmed live, real
  13-host scan with MAC/vendor
* [x] Port scanner (port-range scan of a host) -- confirmed live, real
  open ports found (SSH/DNS/HTTP) on a MikroTik router
* [x] MNDP discovery -- confirmed with a real MikroTik neighbor
* [x] DHCP lease details (server, lease time, domain) alongside the
  existing gateway/DNS -- confirmed live
* [x] Traffic statistics -- confirmed live (own dedicated Traffic page)
* [x] Top talkers -- ranked by live bytes/s, confirmed live
* [x] MTR / traceroute -- confirmed live, real multi-hop trace

### Version 0.3

Industrial Ethernet support:

* [ ] PROFINET DCP discovery
* [ ] PROFINET traffic detection
* [ ] Siemens S7 diagnostics
* [x] Modbus TCP diagnostics (read-only: coils, discrete inputs,
  holding/input registers) -- protocol logic confirmed against a
  local test server (correct register/coil decoding, exception
  handling); **not yet confirmed against a real Modbus device**, none
  available to test against yet
* [ ] Industrial device identification

## Safety

LanPi should default to passive operation whenever possible.

Functions capable of modifying network devices, including PROFINET DCP configuration commands, should not be enabled by default.

The management Wi-Fi interface should be protected from configuration changes performed by Ethernet diagnostic functions.

## Related Projects

LanPi is inspired in part by existing Raspberry Pi network diagnostic projects such as PiScout / RaspberryFluke and NetPi.

Where third-party source code is reused, the original copyright notices and license requirements must be preserved.

## License

LanPi is released under the MIT License.
