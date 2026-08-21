"""Shared packet-capture dispatcher (v0.2.3 Foundation #3/#7).

Before this, LLDP/CDP/MNDP discovery and Traffic Stats each ran their
own dedicated `tcpdump -w -` process against the same interface --
three of them narrowly BPF-filtered, but Traffic Stats' was already
fully unfiltered (it needs every packet anyway), so most of that
traffic was being captured redundantly up to four times over. This
module runs a single unfiltered tcpdump instead and fans every packet
out to whichever listeners have registered a handler.

Consumers call register_handler(callable) with a function taking one
raw Ethernet frame (bytes -- a pcap record's payload, no pcap headers)
and call start_listener(interface), same names/shapes each module
already had for their own now-removed capture loop -- both idempotent,
safe to call from every consumer's own start_listener(). One handler
raising doesn't stop the others (each handler's own listener used to
swallow its own tcpdump/parse failures the same way before this
consolidation).

The dispatcher only ever captures on one interface at a time -- this
project only ever uses one (the TEST PORT, eth0), matching every other
single-interface assumption already baked into this codebase (see
TEST_PORT_INTERFACE in backend/api/routes).

Trade-off: LLDP/CDP/MNDP used to filter at the kernel/BPF level
(cheap, narrow tcpdump filters); now every listener receives every
packet and filters in Python instead (one ethertype/dst-mac comparison
each -- negligible per-packet cost). This doesn't increase capture
volume (Traffic Stats already saw everything); it just moves where the
filtering happens, from four tcpdump processes to one.

get_health() exists so a dispatcher failure -- which now silently
breaks all four listeners at once, a new failure mode this
consolidation introduces -- is something the API can actually report
on, instead of each listener just quietly going stale.
"""

from __future__ import annotations

import shutil
import struct
import subprocess
import threading
import time
from typing import Callable

_TCPDUMP_CANDIDATES = ["/usr/bin/tcpdump", "/usr/sbin/tcpdump", "tcpdump"]
_PCAP_GLOBAL_HEADER_LEN = 24
_PCAP_RECORD_HEADER_LEN = 16
_RESTART_DELAY_SECONDS = 5

_lock = threading.Lock()
_handlers: list[Callable[[bytes], None]] = []
_started = False
_health = {
    "tcpdump_available": None,  # None until the first capture attempt
    "capture_running": False,  # tcpdump currently alive and streaming
    "last_packet_at": None,
}


def _find_tcpdump() -> str | None:
    for candidate in _TCPDUMP_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def register_handler(handler: Callable[[bytes], None]) -> None:
    with _lock:
        _handlers.append(handler)


def _dispatch(packet: bytes) -> None:
    with _lock:
        _health["last_packet_at"] = time.time()
        handlers = list(_handlers)
    for handler in handlers:
        try:
            handler(packet)
        except Exception:
            pass


def _read_exact(stream, count: int) -> bytes | None:
    data = b""
    while len(data) < count:
        chunk = stream.read(count - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def _capture_loop(interface: str) -> None:
    tcpdump = _find_tcpdump()
    with _lock:
        _health["tcpdump_available"] = tcpdump is not None
    if not tcpdump:
        return

    while True:
        proc = None
        try:
            proc = subprocess.Popen(
                [tcpdump, "-i", interface, "-U", "-nn", "-w", "-"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            stdout = proc.stdout
            if _read_exact(stdout, _PCAP_GLOBAL_HEADER_LEN) is None:
                continue

            with _lock:
                _health["capture_running"] = True

            while True:
                record_header = _read_exact(stdout, _PCAP_RECORD_HEADER_LEN)
                if record_header is None:
                    break
                _, _, incl_len, _ = struct.unpack("<IIII", record_header)
                packet = _read_exact(stdout, incl_len)
                if packet is None:
                    break
                _dispatch(packet)
        except Exception:
            pass
        finally:
            with _lock:
                _health["capture_running"] = False
            if proc is not None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    pass
        time.sleep(_RESTART_DELAY_SECONDS)


def start_listener(interface: str = "eth0") -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    thread = threading.Thread(target=_capture_loop, args=(interface,), daemon=True)
    thread.start()


def get_health() -> dict:
    with _lock:
        health = dict(_health)
    if health["last_packet_at"] is not None:
        health["seconds_since_last_packet"] = round(time.time() - health["last_packet_at"], 1)
    else:
        health["seconds_since_last_packet"] = None
    return health
