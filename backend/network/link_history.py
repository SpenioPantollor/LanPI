"""Ethernet link event history for the TEST PORT (eth0) (V0.3 backlog
item -- link event history).

link.get_link_status() only ever returns the current snapshot -- there
was no way to tell whether the link flapped five minutes ago or has
been rock solid since boot. This module polls that same snapshot on a
fixed interval from a background thread and records an event only
when the fields that actually describe "the link" change (presence,
operstate, link_detected, speed_mbps, duplex) -- not on every poll,
and never on the RX/TX counters (which change on essentially every
poll by definition and would flood the log with noise rather than
mark an actual event).

Same background-thread-plus-cache shape as backend/capture/traffic_stats.py
and backend/discovery/*.py, but polling `ip`/`ethtool` on a timer
instead of reacting to captured packets -- link state changes aren't
observable from a packet capture the way ARP/LLDP/etc. are.
"""

from __future__ import annotations

import threading
import time
from collections import deque

from backend.network import link

_POLL_INTERVAL_SECONDS = 2.0
_MAX_EVENTS = 500

_TRACKED_FIELDS = ("present", "operstate", "link_detected", "speed_mbps", "duplex")

_lock = threading.Lock()
_events: deque = deque(maxlen=_MAX_EVENTS)
_last_snapshot: dict | None = None
_started_interfaces: set[str] = set()


def _relevant(status: dict) -> dict:
    return {field: status.get(field) for field in _TRACKED_FIELDS}


def _poll_once(interface: str) -> None:
    global _last_snapshot
    current = _relevant(link.get_link_status(interface))
    with _lock:
        if _last_snapshot is None or current != _last_snapshot:
            _events.append({"timestamp": time.time(), **current})
            _last_snapshot = current


def _watch_loop(interface: str) -> None:
    while True:
        _poll_once(interface)
        time.sleep(_POLL_INTERVAL_SECONDS)


def start_listener(interface: str = "eth0") -> None:
    with _lock:
        if interface in _started_interfaces:
            return
        _started_interfaces.add(interface)
    threading.Thread(target=_watch_loop, args=(interface,), daemon=True).start()


def get_history() -> dict:
    with _lock:
        return {"events": list(_events)}


def reset() -> dict:
    with _lock:
        _events.clear()
        global _last_snapshot
        _last_snapshot = None
    return {"ok": True, "message": "link history reset"}
