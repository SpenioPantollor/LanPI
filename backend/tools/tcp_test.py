"""TCP port connectivity test, sourced from the TEST PORT (eth0).

Binds the outbound socket to eth0's current address before connecting,
AND to the eth0 device itself via SO_BINDTODEVICE. The address-only
bind alone isn't enough: when eth0 and wlan0 both have a route to the
same subnet (e.g. this dev rig's eth0 test switch uplinks into the
same LAN as wlan0), Linux's weak-host-model routing picks the
lower-metric interface for the destination regardless of which
address the socket is bound to, so an address-only-bound socket can
silently transmit out wlan0 while still claiming eth0's source IP
(confirmed live 2026-08-22 via parallel tcpdump on both interfaces
during a real request -- the request left via wlan0, only the reply
came back on eth0). SO_BINDTODEVICE forces the actual physical
interface, closing that gap; it needs CAP_NET_RAW, granted to
lanpi.service via AmbientCapabilities. Requires eth0 to have an
address (DHCP/Static mode); Passive mode has no source address to
bind, so a real TCP handshake can't originate from it.
"""

from __future__ import annotations

import socket
import time

from backend.network import eth0_mode


def _eth0_source_ip() -> str | None:
    mode = eth0_mode.get_mode()
    address = mode.get("address")
    return address.split("/")[0] if address else None


def test_port(host: str, port: int, timeout: float = 3.0) -> dict:
    host = (host or "").strip()
    if not host:
        return {"ok": False, "state": "error", "message": "host is required"}
    if not (1 <= port <= 65535):
        return {"ok": False, "state": "error", "message": "port must be between 1 and 65535"}

    source_ip = _eth0_source_ip()
    if not source_ip:
        return {
            "ok": False,
            "state": "no_source_ip",
            "message": "eth0 has no IP address -- switch to DHCP or Static mode first "
                       "(Passive mode has no source address to test from)",
        }

    start = time.monotonic()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, b"eth0")
        except (OSError, AttributeError):
            # OSError: no CAP_NET_RAW. AttributeError: SO_BINDTODEVICE
            # doesn't exist on this platform (e.g. macOS, used for local
            # dev testing -- it's Linux-only). Either way, falls back to
            # address-only binding.
            pass
        sock.bind((source_ip, 0))
        sock.connect((host, port))
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"ok": True, "state": "open", "latency_ms": latency_ms}
    except socket.timeout:
        return {"ok": True, "state": "timeout", "latency_ms": None}
    except ConnectionRefusedError:
        latency_ms = round((time.monotonic() - start) * 1000, 1)
        return {"ok": True, "state": "closed", "latency_ms": latency_ms}
    except OSError as exc:
        return {"ok": False, "state": "error", "message": str(exc)}
    finally:
        sock.close()
