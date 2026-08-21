# LanPi Architecture

This document describes the planned architecture, boot process, network configuration, web interface structure, and software modules of LanPi.

The document should be updated whenever the architecture of the project changes.

---

# 1. System Overview

LanPi is a Raspberry Pi based portable network analyzer.

The Raspberry Pi has two primary network interfaces with strictly separated purposes:

| Interface | Purpose            | Configuration               |
| --------- | ------------------ | --------------------------- |
| `wlan0`   | Management         | Wi-Fi client or fallback AP |
| `eth0`    | Network under test | Passive, DHCP or Static     |

`wlan0` must never be automatically modified by Ethernet analysis functions.

`eth0` is considered the **TEST PORT** and may be freely reconfigured by LanPi.

Routing or bridging between `wlan0` and `eth0` is disabled by default.

---

# 2. System Boot Sequence

The following operations are performed when Raspberry Pi boots.

```text
POWER ON
   │
   ▼
Raspberry Pi OS
   │
   ▼
NetworkManager starts
   │
   ├── Initialize wlan0
   │
   └── Initialize eth0
   │
   ▼
LanPi Management Network initialization
   │
   ├── Search for configured Wi-Fi networks
   │
   ├── Known Wi-Fi available?
   │       │
   │       ├── YES
   │       │    └── Connect as Wi-Fi client
   │       │
   │       └── NO
   │            └── Start LanPi fallback AP
   │
   ▼
Initialize TEST PORT
   │
   └── eth0 → PASSIVE mode
   │
   ▼
Start LanPi backend
   │
   ▼
Start discovery services
   │
   ├── Link monitor
   │
   ├── LLDP listener
   │
   └── CDP listener
   │
   ▼
Start Web Server
   │
   ▼
LanPi READY
```

The default state of `eth0` after boot should be:

```text
Mode: PASSIVE
IP:   NONE
```

This prevents LanPi from generating unnecessary Layer 3 traffic immediately after being connected to an unknown network.

---

# 3. Management Network

## 3.1 Wi-Fi Client Mode

This is the preferred management mode.

LanPi attempts to connect to previously configured Wi-Fi networks.

Example:

```text
SSID: HomeNetwork
DHCP: Enabled

LanPi:
192.168.1.37
```

LanPi should be accessible using:

```text
http://lanpi.local
```

and:

```text
ssh user@lanpi.local
```

---

# 4. Fallback Access Point

If no configured Wi-Fi network is available during boot, LanPi starts its own access point.

Default configuration:

```text
SSID: LanPi
IP:   172.24.58.1
DHCP: Enabled
```

Example:

```text
Laptop / Phone
      │
      │ Wi-Fi
      ▼
     LanPi
172.24.58.1
```

The user can then access:

```text
http://172.24.58.1
```

The fallback AP must not be bridged to `eth0`.

---

# 5. TEST PORT

`eth0` is exclusively used for network testing.

Three operating modes are planned.

## 5.1 Passive Mode

Default mode.

```text
IP address: NONE
DHCP:       OFF
```

Allowed functions include:

* Link monitoring
* Ethernet statistics
* LLDP
* CDP
* MNDP
* Packet capture
* Passive protocol detection
* PROFINET DCP listening

---

## 5.2 DHCP Mode

LanPi requests network configuration using DHCP.

Information to collect:

```text
Assigned IP
Subnet mask
Gateway
DNS servers
DHCP server
Lease duration
Domain
```

---

## 5.3 Static Mode

User manually specifies:

```text
IP address
Subnet mask
Gateway
DNS
```

Example:

```text
IP:       192.168.20.200
Mask:     255.255.255.0
Gateway:  192.168.20.1
DNS:      optional
```

---

# 6. Web Interface

The LanPi web interface consists of one main dashboard and several functional pages.

```text
LanPi Web
│
├── Dashboard
│
├── Test Port
│
├── Discovery
│
├── Tools
│
├── Traffic
│
├── Capture
│
└── Settings
```

---

# 7. Dashboard

The Dashboard is the default page displayed when opening LanPi.

Its purpose is to provide an immediate overview of the connected network.

## 7.1 Management Status

Display:

```text
Management Mode: Wi-Fi Client
SSID:            HomeNetwork
IP:              192.168.1.37
Internet:        Available
```

or:

```text
Management Mode: Fallback AP
SSID:            LanPi
IP:              172.24.58.1
Internet:        Offline
```

---

## 7.2 TEST PORT Status

Display:

```text
Link:        UP
Speed:       1000 Mbps
Duplex:      Full
Autoneg:     Yes

Mode:        PASSIVE
IP:          None
```

---

## 7.3 Neighbor Information

If LLDP/CDP information is available:

```text
Protocol:       LLDP
Switch:         SW-CORE-01
Port:           Gi1/0/17
Management IP:  192.168.20.2
VLAN:           20
```

---

## 7.4 Traffic Overview

Basic live statistics:

```text
RX:              4.2 Mbps
TX:              12 kbps

Packets/s:       421
Broadcast/s:     32
Multicast/s:     74
```

Future versions may include detected protocols.

---

# 8. Test Port Page

Purpose:

Configure and inspect `eth0`.

Functions:

### Link Information

Display:

* Link state
* Speed
* Duplex
* Auto-negotiation
* MAC address
* MTU
* RX/TX counters
* Errors
* Dropped packets

### IP Configuration

Select:

```text
[ Passive ]
[ DHCP    ]
[ Static  ]
```

Static mode provides fields for:

```text
IP
Mask
Gateway
DNS
```

---

# 9. Discovery Page

Purpose:

Identify devices and infrastructure visible from the TEST PORT.

Initial protocols:

| Protocol | Function                |
| -------- | ----------------------- |
| LLDP     | Switch/device discovery |
| CDP      | Cisco discovery         |
| MNDP     | MikroTik discovery      |
| ARP      | IPv4 host discovery     |

Future:

| Protocol     | Function                   |
| ------------ | -------------------------- |
| PROFINET DCP | PROFINET device discovery  |
| S7           | Siemens PLC identification |

Results should include, when available:

```text
Device name
IP
MAC
Manufacturer
Protocol
Switch port
VLAN
Device type
```

---

# 10. Tools Page

Active diagnostic tools.

Initial tools:

```text
Ping
Continuous Ping
ARP Lookup
ARP Scan
Traceroute
MTR
DNS Lookup
TCP Connection Test
```

TCP test presets:

| Service    | Port |
| ---------- | ---: |
| Siemens S7 |  102 |
| Modbus TCP |  502 |
| HTTP       |   80 |
| HTTPS      |  443 |
| VNC        | 5900 |

Custom TCP ports must also be supported.

Planned for a later version (V0.2):

```text
IP Scanner    - subnet host discovery (which addresses are alive)
Port Scanner  - scan a target across a port range, not just a single
                TCP connection test
```

Both build on `nmap` / `arp-scan`, already part of the planned
software stack (see section on Software Architecture in README.md).
As with all active-scanning functionality, these are not run
automatically -- passive mode stays the default per Rule 4.

Also planned, but **confirmed not supported on the current Pi 3
hardware**:

```text
Cable Diagnostics
  - Wire pair quality (per-pair open / short / OK)
  - Cable length estimation
```

TDR functionality via `ethtool --cable-test`. Checked live:
`ethtool --cable-test eth0` returns "PHY driver does not support
cable testing" -- the Pi 3's Ethernet is a USB-attached `smsc95xx`
adapter with no TDR circuitry, not a native PHY. Raspberry Pi 4 has a
real Gigabit PHY (Broadcom BCM54213PE) and is a more plausible
candidate, but that alone doesn't confirm the Linux PHY driver for
that chip implements the cable-test callbacks -- untested on real Pi
4 hardware. Not planned for any version until confirmed on hardware
actually tested (see README.md Hardware / Cable Diagnostics sections).

---

# 11. Traffic Page

Purpose:

Provide live information about traffic visible on `eth0`.

Initial statistics:

```text
Packets/s
Bytes/s

Broadcast/s
Multicast/s
Unicast/s

RX errors
TX errors
Dropped packets
```

Protocol statistics:

```text
ARP
IPv4
IPv6
DHCP
LLDP
CDP
mDNS
SSDP
```

Future:

```text
PROFINET
S7
Modbus TCP
```

Possible future feature:

### Top Talkers

```text
Device             Packets/s

192.168.1.20          320
192.168.1.30          140
00:0E:8C:xx:xx:xx      90
```

---

# 12. Capture Page

Purpose:

Capture Ethernet traffic for later analysis.

Functions:

```text
START CAPTURE
STOP CAPTURE
```

Display:

```text
Capture status
Duration
Packets captured
File size
```

Filters:

```text
All
ARP
DHCP
LLDP/CDP
Broadcast
PROFINET
S7
Modbus TCP
Custom BPF
```

Captured traffic is saved as:

```text
.pcap
```

Files can be downloaded and opened in Wireshark.

---

# 13. Settings Page

System configuration.

## Wi-Fi

Functions:

```text
Show current connection
Scan Wi-Fi networks
Connect to Wi-Fi
Forget network
Configure fallback AP
```

## System

Display:

```text
Hostname
Raspberry Pi model
CPU temperature
CPU load
Memory usage
Disk usage
Uptime
LanPi version
```

Actions:

```text
Restart LanPi
Reboot Raspberry Pi
Shutdown Raspberry Pi
```

---

# 14. Backend Architecture

The backend is divided into independent functional modules.

```text
backend/
│
├── api/
│
├── network/
│
├── discovery/
│
├── tools/
│
└── capture/
```

Each module should perform one clearly defined function.

For example:

```text
network/link.py
```

is responsible only for Ethernet link information.

```text
discovery/lldp.py
```

is responsible only for LLDP discovery.

```text
tools/ping.py
```

is responsible only for ping operations.

The Web API should interact with these modules instead of directly executing system commands from frontend requests.

---

# 15. Safety Rules

These rules are considered part of the LanPi architecture.

### Rule 1

`wlan0` is the management interface.

Ethernet analysis functions must never modify it.

### Rule 2

`eth0` is the TEST PORT.

Its configuration may be changed by LanPi.

### Rule 3

No bridge between:

```text
wlan0 ↔ eth0
```

unless such functionality is explicitly implemented in the future.

This also covers routing, not just L2 bridging: `eth0` (the TEST PORT)
must never contribute a default route, even when DHCP or a static
config gives it a gateway. A lower-metric eth0 default route can
silently steal the Pi's own outbound traffic away from `wlan0` --
confirmed live during development (see `STATUS.md`), where this cut
off the Pi's own internet/git access until fixed. Enforced via
`ipv4.never-default=yes` on the `lanpi-eth0` NetworkManager profile.

### Rule 4

Default TEST PORT state after boot:

```text
PASSIVE
```

### Rule 5

Potentially destructive industrial protocol functions must not be enabled by default.

For example:

```text
PROFINET DCP Identify → allowed

PROFINET DCP Set      → disabled by default
```

### Rule 6

The web frontend must not execute arbitrary shell commands.

All privileged operations must go through predefined backend functions.

### Rule 7

LanPi's own management interface (port 8000) must not be reachable
from `eth0` (the TEST PORT).

Enforced via `nftables` (`system/nftables.conf`, installed to
`/etc/nftables.conf` by `install.sh`): `iifname "eth0" tcp dport 8000
drop`, nothing else. A device connected to `eth0` for diagnostics has
no legitimate reason to reach LanPi's own web UI/API through the port
under test.

SSH (22) is a deliberate exception -- explicitly kept open on `eth0`
as a recovery path if `wlan0` becomes unreachable (see `STATUS.md`'s
wlan0-recovery-via-eth0 procedure). Only port 8000 is blocked; every
other port on `eth0`, and everything on `wlan0`, is untouched.

---

# 16. Development Priorities

Development should proceed in small functional stages.

```text
PHASE 1
│
├── Raspberry Pi OS
├── Wi-Fi management
├── Git
└── Basic FastAPI server

PHASE 2
│
├── Dashboard
├── eth0 link state
├── speed
├── duplex
└── interface counters

PHASE 3
│
├── Passive mode
├── DHCP mode
└── Static mode

PHASE 4
│
├── Ping
├── TCP test
├── ARP
├── IP scanner
├── Port scanner
└── basic network tools

PHASE 5
│
├── LLDP
├── CDP
└── MNDP

PHASE 6
│
├── Packet capture
└── Traffic statistics

PHASE 7
│
└── Industrial Ethernet
    ├── PROFINET
    ├── S7
    └── Modbus TCP
```

---

# 17. Architecture Decisions

Major architectural decisions should be documented here.

| Decision                | Reason                                              |
| ----------------------- | --------------------------------------------------- |
| Raspberry Pi 4 (planned), Pi 3 (actual so far) | Pi 4 chosen for Gigabit Ethernet; development/testing has so far only happened on a Pi 3 (100 Mbps Ethernet, 1 GB RAM) -- see README.md Hardware section |
| Raspberry Pi OS Lite    | Lightweight base system                             |
| `wlan0` for management  | TEST PORT can be reconfigured without losing access |
| `eth0` as TEST PORT     | Dedicated physical Ethernet interface               |
| Wi-Fi fallback AP       | Field operation without external infrastructure     |
| Passive mode by default | Avoid unnecessary traffic on unknown networks       |
| FastAPI backend         | Lightweight Python API                              |
| Simple HTML/JS frontend | Low resource usage and simple maintenance           |
| PCAP export             | Compatibility with Wireshark                        |
| Modular analyzers       | Easier AI-assisted development and testing          |

---

# 18. Current Development State

This section should always describe what actually works. For day-to-day
detail (what's deployed, what's been verified against real hardware,
known gaps) see `STATUS.md` at the repo root -- that file is updated
every session and is the authoritative source; this checklist is the
coarse long-term summary.

```text
[x] Raspberry Pi OS installed
[x] Git repository configured
[x] Wi-Fi client configured
[x] Fallback AP implemented (hostapd-based)
[x] FastAPI installed
[x] Web interface available
[x] TEST PORT link monitoring
[x] TEST PORT IP configuration
[x] LLDP
[x] CDP
[x] MNDP
[x] Ping
[x] ARP scan
[x] TCP port test
[x] Packet capture
[x] MTR / traceroute
[x] Traffic analyzer (packet/byte counters, top talkers, own Traffic page)
[x] IP scanner
[x] Port scanner (range scan -- distinct from the single TCP port test above)
[ ] PROFINET (basic detection exists in the traffic analyzer above; full
    DCP discovery/diagnostics still planned)
[x] Modbus TCP (read-only: coils, discrete inputs, holding/input
    registers; device templates for known meters)
```
