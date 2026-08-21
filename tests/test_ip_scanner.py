"""Tests for backend/tools/ip_scanner.py's start()-time validation, which
runs before any nmap/sudo subprocess call."""
from backend.tools import ip_scanner


def test_start_rejects_empty_target():
    result = ip_scanner.start("")
    assert result == {"ok": False, "message": "target network/range is required"}


def test_start_rejects_target_starting_with_dash():
    result = ip_scanner.start("-oScript.nse")
    assert result == {"ok": False, "message": "invalid target"}
