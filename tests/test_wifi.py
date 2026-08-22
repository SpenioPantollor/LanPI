"""Tests for backend/network/wifi.py's retry_known() -- the manual
"try reconnecting to a known network now" action added 2026-08-22.

Only retry_known()'s branching logic is covered here (via monkeypatched
ap.is_active()/deactivate()/activate() and wifi.get_status()) -- like
the rest of this module, actual nmcli/hostapd behavior relies on live
verification on the Pi, not unit tests (no real wlan0/nmcli in this
environment to test against).
"""
import pytest

from backend.network import wifi


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # retry_known() polls once a (real) second while waiting -- replace
    # with a no-op so tests using a tiny wait_seconds run instantly.
    monkeypatch.setattr(wifi.time, "sleep", lambda seconds: None)


def test_retry_known_noop_when_ap_not_active(monkeypatch):
    monkeypatch.setattr(wifi.ap, "is_active", lambda: False)
    result = wifi.retry_known()
    assert result == {"ok": False, "message": "fallback AP is not active"}


def test_retry_known_reports_deactivate_failure(monkeypatch):
    monkeypatch.setattr(wifi.ap, "is_active", lambda: True)
    monkeypatch.setattr(wifi.ap, "deactivate", lambda: {"ok": False, "message": "boom"})
    result = wifi.retry_known()
    assert result["ok"] is False
    assert "boom" in result["message"]


def test_retry_known_succeeds_when_client_reconnects(monkeypatch):
    monkeypatch.setattr(wifi.ap, "is_active", lambda: True)
    monkeypatch.setattr(wifi.ap, "deactivate", lambda: {"ok": True, "message": "down"})
    activate_calls = []
    monkeypatch.setattr(wifi.ap, "activate", lambda: activate_calls.append(1))
    monkeypatch.setattr(
        wifi, "get_status", lambda: {"mode": "client", "connected": True, "ssid": "Pypas"}
    )

    result = wifi.retry_known(wait_seconds=0.01)

    assert result == {"ok": True, "message": "connected to Pypas"}
    assert activate_calls == []  # AP must NOT be restored on success


def test_retry_known_restores_ap_after_timeout(monkeypatch):
    monkeypatch.setattr(wifi.ap, "is_active", lambda: True)
    monkeypatch.setattr(wifi.ap, "deactivate", lambda: {"ok": True, "message": "down"})
    activate_calls = []
    monkeypatch.setattr(wifi.ap, "activate", lambda: activate_calls.append(1))
    monkeypatch.setattr(
        wifi, "get_status", lambda: {"mode": "none", "connected": False, "ssid": None}
    )

    result = wifi.retry_known(wait_seconds=0.01)

    assert result["ok"] is True
    assert "restored" in result["message"]
    assert activate_calls == [1]  # AP restored so the device isn't stranded
