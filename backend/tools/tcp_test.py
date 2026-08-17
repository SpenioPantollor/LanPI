"""TCP port connectivity test, sourced from the TEST PORT (eth0).

Binds the outbound socket to eth0's current address before connecting.
eth0 deliberately has no default route (ARCHITECTURE.MD Rule 3 --
ipv4.never-default), so an unbound socket.connect() to a host outside
eth0's local subnet would silently go out wlan0's default route
instead, defeating the entire point of testing a port "on the network
under test". Requires eth0 to have an address (DHCP/Static mode);
Passive mode has no source address to bind, so a real TCP handshake
can't originate from it.
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
