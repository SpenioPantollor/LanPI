# LanPI

**LanPi** is a portable Raspberry Pi-based Ethernet network analysis and diagnostic tool.

The goal of the project is to provide a simple web-based interface for network troubleshooting without requiring a dedicated display or keyboard.

LanPi uses Wi-Fi for management while keeping the physical Ethernet interface dedicated to the network being tested.

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

## Planned Features

### Ethernet Port Status

* Link status
* Link speed
* Duplex
* Auto-negotiation status
* MAC address
* MTU
* Interface statistics

### Cable Diagnostics (not supported on the current development hardware)

* Wire pair quality (per-pair open / short / OK)
* Cable length estimation

This is TDR (Time Domain Reflectometry) functionality, exposed via
`ethtool --cable-test` when the Ethernet PHY supports it. **Checked
against the actual Pi 3 in use: not supported** --
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

### IP Configuration

* Passive mode
* DHCP client
* Static IPv4 configuration
* Gateway configuration
* DNS configuration
* DHCP lease information

### Network Discovery

* LLDP
* CDP
* MNDP
* ARP discovery

Future industrial discovery:

* PROFINET DCP
* Siemens device identification

### Diagnostic Tools

* Ping
* Continuous ping
* ARP lookup
* ARP network scan
* IP scanner (subnet host discovery — which addresses are alive)
* Port scanner (scan a host across a port range, not just a single
  TCP connection test)
* Traceroute / MTR
* DNS lookup
* TCP connection test

Planned protocol presets:

| Protocol   | TCP Port |
| ---------- | -------: |
| Siemens S7 |      102 |
| Modbus TCP |      502 |
| HTTP       |       80 |
| HTTPS      |      443 |
| VNC        |     5900 |

### Passive Traffic Analysis

Planned live statistics include:

* Packets per second
* Broadcast traffic
* Multicast traffic
* ARP
* IPv4
* IPv6
* DHCP
* LLDP
* CDP
* mDNS
* SSDP
* Top talkers

Future industrial protocol analysis:

* PROFINET
* S7
* Modbus TCP

### Packet Capture

LanPi will provide packet capture directly from the web interface.

Planned functionality:

* Start capture
* Stop capture
* Capture duration
* Protocol filters
* BPF custom filter
* Download PCAP files
* Open captures later using Wireshark

Planned presets:

* All traffic
* Broadcast / multicast
* ARP
* DHCP
* LLDP / CDP
* PROFINET
* Siemens S7
* Modbus TCP

## Software Architecture

The current planned software stack is:

* Raspberry Pi OS Lite
* Python
* FastAPI
* NetworkManager
* HTML / CSS / JavaScript
* tcpdump / tshark
* ethtool
* iproute2
* nmap
* arp-scan
* mtr

The frontend is intentionally planned without a large JavaScript framework to keep the system lightweight.

## Development Approach

LanPi is an experimental **vibe-coded / AI-assisted project**.

A significant portion of the project is developed with the assistance of AI coding tools, while the overall architecture, requirements, testing, and real-world network validation are performed by the project maintainer.

AI-generated code should not be assumed to be correct or secure by default. Network configuration, packet handling, protocol implementations, and other potentially disruptive functionality should be reviewed and tested before use on production networks.

The project is also intended as a practical experiment in building a useful network engineering tool through AI-assisted development.

## Repository Structure

```text
lanpi/
├── README.md
├── LICENSE
├── .gitignore
├── requirements.txt
│
├── backend/
│   ├── main.py
│   ├── api/
│   ├── network/
│   ├── discovery/
│   ├── tools/
│   └── capture/
│
├── frontend/
│   ├── index.html
│   ├── app.js
│   └── style.css
│
├── system/
│   ├── lanpi.service
│   └── install.sh
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
* [ ] Port scanner (port-range scan of a host)
* [x] MNDP discovery -- confirmed with a real MikroTik neighbor
* [x] DHCP lease details (server, lease time, domain) alongside the
  existing gateway/DNS -- confirmed live
* [x] Traffic statistics -- confirmed live (own dedicated Traffic page)
* [x] Top talkers -- ranked by live bytes/s, confirmed live
* [x] MTR / traceroute -- confirmed live, real multi-hop trace
* [ ] Improved packet capture filters

### Version 0.3

Industrial Ethernet support:

* [ ] PROFINET DCP discovery
* [ ] PROFINET traffic detection
* [ ] Siemens S7 diagnostics
* [ ] Modbus TCP diagnostics
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
