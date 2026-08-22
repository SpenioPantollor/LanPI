"""Tests for backend/network/link_history.py's change-detection and
event-log bookkeeping.

Like test_dispatcher.py, the real background thread (_watch_loop) is
not exercised here -- it never stops by design (matches
lldp/cdp/mndp/traffic_stats/dispatcher, meant to run for the app's
whole lifetime). What's tested is _poll_once, the single-iteration
step _watch_loop repeats forever, with link.get_link_status()
monkeypatched to controlled snapshots.
"""
from backend.network import link, link_history


def _reset(monkeypatch, status):
    monkeypatch.setattr(link, "get_link_status", lambda interface: status)
    with link_history._lock:
        link_history._events.clear()
        link_history._last_snapshot = None


def _status(**overrides):
    base = {
        "interface": "eth0", "present": True, "operstate": "UP", "link_detected": True,
        "speed_mbps": 1000, "duplex": "full", "rx_bytes": 0, "tx_bytes": 0,
    }
    base.update(overrides)
    return base


def test_first_poll_always_records_an_event(monkeypatch):
    _reset(monkeypatch, _status())

    link_history._poll_once("eth0")

    events = link_history.get_history()["events"]
    assert len(events) == 1
    assert events[0]["operstate"] == "UP"
    assert events[0]["link_detected"] is True


def test_unchanged_status_does_not_add_a_second_event(monkeypatch):
    _reset(monkeypatch, _status())
    link_history._poll_once("eth0")

    link_history._poll_once("eth0")

    assert len(link_history.get_history()["events"]) == 1


def test_rx_tx_byte_changes_alone_do_not_add_an_event(monkeypatch):
    _reset(monkeypatch, _status(rx_bytes=100))
    link_history._poll_once("eth0")

    monkeypatch.setattr(link, "get_link_status", lambda interface: _status(rx_bytes=9999))
    link_history._poll_once("eth0")

    assert len(link_history.get_history()["events"]) == 1


def test_link_down_transition_adds_an_event(monkeypatch):
    _reset(monkeypatch, _status())
    link_history._poll_once("eth0")

    monkeypatch.setattr(
        link, "get_link_status",
        lambda interface: _status(operstate="DOWN", link_detected=False, speed_mbps=None, duplex=None),
    )
    link_history._poll_once("eth0")

    events = link_history.get_history()["events"]
    assert len(events) == 2
    assert events[-1]["operstate"] == "DOWN"
    assert events[-1]["link_detected"] is False


def test_speed_change_alone_adds_an_event(monkeypatch):
    _reset(monkeypatch, _status(speed_mbps=1000))
    link_history._poll_once("eth0")

    monkeypatch.setattr(link, "get_link_status", lambda interface: _status(speed_mbps=100))
    link_history._poll_once("eth0")

    events = link_history.get_history()["events"]
    assert len(events) == 2
    assert events[-1]["speed_mbps"] == 100


def test_interface_disappearing_adds_an_event(monkeypatch):
    _reset(monkeypatch, _status())
    link_history._poll_once("eth0")

    monkeypatch.setattr(
        link, "get_link_status",
        lambda interface: {"interface": "eth0", "present": False, "operstate": None,
                            "link_detected": None, "speed_mbps": None, "duplex": None},
    )
    link_history._poll_once("eth0")

    events = link_history.get_history()["events"]
    assert len(events) == 2
    assert events[-1]["present"] is False


def test_reset_clears_events_and_forces_a_fresh_baseline(monkeypatch):
    _reset(monkeypatch, _status())
    link_history._poll_once("eth0")

    result = link_history.reset()

    assert result == {"ok": True, "message": "link history reset"}
    assert link_history.get_history()["events"] == []

    link_history._poll_once("eth0")
    assert len(link_history.get_history()["events"]) == 1


def test_events_are_bounded_by_max_events(monkeypatch):
    _reset(monkeypatch, _status())
    for speed in range(link_history._MAX_EVENTS + 20):
        monkeypatch.setattr(link, "get_link_status", lambda interface, s=speed: _status(speed_mbps=s))
        link_history._poll_once("eth0")

    events = link_history.get_history()["events"]
    assert len(events) == link_history._MAX_EVENTS
    assert events[-1]["speed_mbps"] == link_history._MAX_EVENTS + 19
