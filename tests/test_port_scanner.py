"""Tests for backend/tools/port_scanner.py's pure validation logic.

_parse_port_range and start()'s guard clauses run before any nmap/sudo
subprocess call, so they're testable without those tools present.
"""
from backend.tools import port_scanner


def test_parse_port_range_valid():
    assert port_scanner._parse_port_range("1-1024") == (1, 1024)


def test_parse_port_range_full_span_valid():
    # The port scanner page's own "All (1-65535)" preset -- a previous
    # bug (a since-removed _MAX_RANGE_SIZE cap) made this always fail.
    assert port_scanner._parse_port_range("1-65535") == (1, 65535)


def test_parse_port_range_rejects_non_numeric():
    assert port_scanner._parse_port_range("abc-def") is None


def test_parse_port_range_rejects_start_below_one():
    assert port_scanner._parse_port_range("0-100") is None


def test_parse_port_range_rejects_start_after_end():
    assert port_scanner._parse_port_range("100-50") is None


def test_parse_port_range_rejects_end_above_65535():
    assert port_scanner._parse_port_range("1-65536") is None


def test_parse_port_range_rejects_empty():
    assert port_scanner._parse_port_range("") is None


def test_start_rejects_empty_host():
    result = port_scanner.start("", "1-1024")
    assert result == {"ok": False, "message": "host is required"}


def test_start_rejects_host_starting_with_dash():
    result = port_scanner.start("-oScript.nse", "1-1024")
    assert result == {"ok": False, "message": "invalid host"}


def test_start_rejects_bad_port_range():
    result = port_scanner.start("192.168.1.1", "not-a-range")
    assert result["ok"] is False
    assert "1-65535" in result["message"]
