"""Tests for backend/capture/dispatcher.py's handler registry and
health reporting.

The real subprocess-based capture loop (start_listener/_capture_loop)
is deliberately not exercised here with a real spawned process --
unlike pcap.py, dispatcher.py has no stop() by design (it matches
lldp/cdp/mndp/traffic_stats, meant to run for the app's whole
lifetime), so starting a real background capture thread in a test
without a clean way to tear it down risks leaking an orphan subprocess
-- exactly the mtr-packet bug this project already hit and fixed once
this session. The real end-to-end capture path (spawn tcpdump, parse
pcap framing, dispatch to every listener) is covered by live
verification on the Pi instead (see STATUS.md), the same tier already
used for mtr.py/ip_scanner.py/port_scanner.py's own subprocess
lifecycles. What's tested here -- the handler registry and health
bookkeeping -- is everything reachable without spawning a process.
"""
import time

import pytest

from backend.capture import dispatcher


@pytest.fixture(autouse=True)
def _reset_dispatcher_state():
    with dispatcher._lock:
        dispatcher._handlers.clear()
        dispatcher._started = False
        dispatcher._health.update(
            {"tcpdump_available": None, "capture_running": False, "last_packet_at": None}
        )
    yield


def test_register_handler_receives_dispatched_packets():
    received = []
    dispatcher.register_handler(received.append)

    dispatcher._dispatch(b"packet-one")

    assert received == [b"packet-one"]


def test_dispatch_calls_every_registered_handler():
    calls = []
    dispatcher.register_handler(lambda p: calls.append(("a", p)))
    dispatcher.register_handler(lambda p: calls.append(("b", p)))

    dispatcher._dispatch(b"x")

    assert calls == [("a", b"x"), ("b", b"x")]


def test_a_handler_raising_does_not_block_the_others():
    calls = []

    def bad_handler(packet):
        raise ValueError("boom")

    dispatcher.register_handler(bad_handler)
    dispatcher.register_handler(calls.append)

    dispatcher._dispatch(b"x")  # must not raise

    assert calls == [b"x"]


def test_dispatch_updates_last_packet_at():
    before = time.time()

    dispatcher._dispatch(b"x")
    health = dispatcher.get_health()

    assert health["last_packet_at"] >= before
    assert health["seconds_since_last_packet"] is not None
    assert health["seconds_since_last_packet"] >= 0


def test_get_health_before_any_packet():
    health = dispatcher.get_health()

    assert health["last_packet_at"] is None
    assert health["seconds_since_last_packet"] is None
    assert health["capture_running"] is False
    assert health["tcpdump_available"] is None
