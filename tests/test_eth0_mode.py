"""Tests for backend/network/eth0_mode.py.

get_mode()'s nmcli-output parsing is tested by faking _run() with
canned terse-mode output, since nmcli itself isn't available (or
meaningful) off the real Pi. set_static()'s CIDR validation happens
before any subprocess call, so it's tested directly.
"""
from types import SimpleNamespace

from backend.network import eth0_mode


def _fake_run(device_show_stdout: str, method_stdout: str = "ipv4.method:auto"):
    def run(args):
        if "device" in args:
            return SimpleNamespace(returncode=0, stdout=device_show_stdout)
        if "ipv4.method" in args:
            return SimpleNamespace(returncode=0, stdout=method_stdout)
        return SimpleNamespace(returncode=1, stdout="")
    return run


def test_get_mode_passive_when_no_connection(monkeypatch):
    monkeypatch.setattr(eth0_mode, "_run", _fake_run("GENERAL.CONNECTION:--\n"))
    mode = eth0_mode.get_mode()
    assert mode == {
        "available": True, "mode": "passive",
        "address": None, "gateway": None, "dns": [],
        "lease_time_seconds": None, "dhcp_server": None, "domain_name": None,
    }


def test_get_mode_dhcp_parses_options_and_falls_back_to_dhcp_gateway(monkeypatch):
    device_show = (
        "GENERAL.CONNECTION:lanpi-eth0\n"
        "IP4.ADDRESS[1]:192.168.1.50/24\n"
        "IP4.GATEWAY:\n"
        "IP4.DNS[1]:8.8.8.8\n"
        "DHCP4.OPTION[1]:routers = 192.168.1.1\n"
        "DHCP4.OPTION[2]:dhcp_lease_time = 43200\n"
        "DHCP4.OPTION[3]:dhcp_server_identifier = 192.168.1.1\n"
        "DHCP4.OPTION[4]:domain_name = example.com\n"
    )
    monkeypatch.setattr(eth0_mode, "_run", _fake_run(device_show, "ipv4.method:auto"))

    mode = eth0_mode.get_mode()
    assert mode == {
        "available": True,
        "mode": "dhcp",
        "address": "192.168.1.50/24",
        "gateway": "192.168.1.1",  # from DHCP4.OPTION[routers], since IP4.GATEWAY is empty in this fake output
        "dns": ["8.8.8.8"],
        "lease_time_seconds": 43200,
        "dhcp_server": "192.168.1.1",
        "domain_name": "example.com",
    }


def test_get_mode_static_reports_manual_method(monkeypatch):
    device_show = (
        "GENERAL.CONNECTION:lanpi-eth0\n"
        "IP4.ADDRESS[1]:192.168.20.200/24\n"
        "IP4.GATEWAY:\n"
    )
    monkeypatch.setattr(eth0_mode, "_run", _fake_run(device_show, "ipv4.method:manual"))

    mode = eth0_mode.get_mode()
    assert mode["mode"] == "static"
    assert mode["address"] == "192.168.20.200/24"
    assert mode["lease_time_seconds"] is None  # only populated in dhcp mode


def test_get_mode_unavailable_when_nmcli_missing(monkeypatch):
    monkeypatch.setattr(eth0_mode, "_run", lambda args: None)
    assert eth0_mode.get_mode() == {"available": False}


def test_set_static_rejects_missing_address():
    result = eth0_mode.set_static("")
    assert result == {"ok": False, "message": "address must be in CIDR form, e.g. 192.168.20.200/24"}


def test_set_static_rejects_address_without_cidr_suffix():
    result = eth0_mode.set_static("192.168.20.200")
    assert result["ok"] is False
    assert "CIDR" in result["message"]


def test_set_static_reports_profile_creation_failure(monkeypatch):
    monkeypatch.setattr(eth0_mode, "_ensure_profile", lambda: False)
    result = eth0_mode.set_static("192.168.20.200/24")
    assert result == {"ok": False, "message": "could not create lanpi-eth0 profile"}
