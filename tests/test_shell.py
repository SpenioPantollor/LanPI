"""Tests for backend/shell.py -- the shared binary-discovery and
subprocess.run wrapper factored out of ~12 near-identical copies
across backend/network/*.py, backend/tools/*.py, backend/capture/*.py
(v0.2.3 Foundation #10)."""
import subprocess

from backend import shell


def test_find_binary_returns_first_match():
    assert shell.find_binary(["/no/such/binary", "python3"]) is not None


def test_find_binary_returns_none_when_nothing_found():
    assert shell.find_binary(["/no/such/binary", "also-not-a-real-binary-xyz"]) is None


def test_run_returns_completed_process_on_success():
    result = shell.run(["python3", "-c", "print('hello')"])
    assert result is not None
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


def test_run_returns_none_on_nonexistent_binary():
    assert shell.run(["/no/such/binary"]) is None


def test_run_returns_none_on_timeout():
    assert shell.run(["python3", "-c", "import time; time.sleep(5)"], timeout=0.1) is None


def test_run_captures_nonzero_exit_without_raising():
    result = shell.run(["python3", "-c", "import sys; sys.exit(3)"])
    assert result is not None
    assert result.returncode == 3


def test_run_privileged_returns_none_without_sudo(monkeypatch):
    monkeypatch.setattr(shell, "find_binary", lambda candidates: None)
    assert shell.run_privileged(["echo", "hi"]) is None


def test_run_privileged_prefixes_sudo(monkeypatch):
    captured = {}

    def fake_run(args, timeout=20.0):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(shell, "find_binary", lambda candidates: "/usr/bin/sudo")
    monkeypatch.setattr(shell, "run", fake_run)

    shell.run_privileged(["nmcli", "device", "show"])

    assert captured["args"] == ["/usr/bin/sudo", "nmcli", "device", "show"]
