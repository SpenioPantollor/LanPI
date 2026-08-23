"""Tests for backend/network/link.py's PHY-statistics parsing, added
2026-08-22 alongside `ethtool --phy-statistics` support (a working,
no-kernel-patch-needed proxy for cable/signal quality, unlike the
still-unsupported `ethtool --cable-test` -- see README's Cable
Diagnostics section)."""
from backend.network import link


class _FakeResult:
    def __init__(self, stdout):
        self.stdout = stdout


def test_parses_real_phy_statistics_output(monkeypatch):
    output = (
        "PHY statistics:\n"
        "     phy_receive_errors: 0\n"
        "     phy_serdes_ber_errors: 0\n"
        "     phy_false_carrier_sense_errors: 0\n"
        "     phy_local_rcvr_nok: 3\n"
        "     phy_remote_rcv_nok: 0\n"
        "     phy_lpi_count: 12\n"
    )
    monkeypatch.setattr(link.shell, "find_binary", lambda candidates: "/usr/sbin/ethtool")
    monkeypatch.setattr(link.shell, "run", lambda cmd, timeout=5: _FakeResult(output))

    stats = link._get_phy_statistics("eth0")

    assert stats == {
        "phy_receive_errors": 0,
        "phy_serdes_ber_errors": 0,
        "phy_false_carrier_sense_errors": 0,
        "phy_local_rcvr_nok": 3,
        "phy_remote_rcv_nok": 0,
        "phy_lpi_count": 12,
    }


def test_unsupported_phy_returns_empty_dict_not_nulls(monkeypatch):
    # e.g. the Pi 3's USB smsc95xx adapter -- ethtool exits with an
    # error/no matching lines rather than a clean key: value block.
    monkeypatch.setattr(link.shell, "find_binary", lambda candidates: "/usr/sbin/ethtool")
    monkeypatch.setattr(
        link.shell, "run", lambda cmd, timeout=5: _FakeResult("Cannot get PHY statistics\n")
    )

    assert link._get_phy_statistics("eth0") == {}


def test_no_ethtool_binary_returns_empty_dict(monkeypatch):
    monkeypatch.setattr(link.shell, "find_binary", lambda candidates: None)
    assert link._get_phy_statistics("eth0") == {}


def test_ignores_unrecognized_lines(monkeypatch):
    # Guards against accidentally picking up unrelated lines that
    # happen to match "word: number" (e.g. a stray NIC-statistics-style
    # line) -- only the known PHY stat keys should ever be included.
    output = "PHY statistics:\n     phy_receive_errors: 1\n     totally_unrelated_stat: 5\n"
    monkeypatch.setattr(link.shell, "find_binary", lambda candidates: "/usr/sbin/ethtool")
    monkeypatch.setattr(link.shell, "run", lambda cmd, timeout=5: _FakeResult(output))

    stats = link._get_phy_statistics("eth0")

    assert stats == {"phy_receive_errors": 1}
