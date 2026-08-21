"""Tests for backend/capture/pcap.py's per-file rotation and total
storage cap (v0.2.3 Foundation item #6).

Uses tests/fixtures/fake_tcpdump.py (a real subprocess that just writes
growing bytes) instead of real tcpdump, so rotation/spawn/SIGTERM are
exercised end-to-end without needing real tcpdump or network traffic.
"""
import os
import time
from pathlib import Path

import pytest

from backend.capture import pcap

_FAKE_TCPDUMP = Path(__file__).parent / "fixtures" / "fake_tcpdump.py"


@pytest.fixture(autouse=True)
def _isolated_capture_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pcap, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(pcap, "_find_tcpdump", lambda: str(_FAKE_TCPDUMP))
    with pcap._lock:
        pcap._state.update(
            {
                "running": False, "filename": None, "part": None, "interface": None,
                "bpf_filter": None, "started_at": None, "duration_seconds": None,
                "process": None, "_stop_requested": False,
            }
        )
    yield
    pcap.stop()
    time.sleep(0.2)  # let the session thread notice and exit before the next test


def _wait_until(predicate, timeout=5.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_start_creates_first_part_file(tmp_path):
    result = pcap.start("eth0")
    assert result["ok"] is True
    assert result["filename"].endswith("-001.pcap")
    assert _wait_until(lambda: (tmp_path / result["filename"]).exists())


def test_rotation_creates_a_new_part_past_the_size_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(pcap, "_ROTATE_BYTES", 20_000)
    monkeypatch.setattr(pcap, "_ROTATE_POLL_SECONDS", 0.05)

    result = pcap.start("eth0")
    assert result["ok"] is True
    assert _wait_until(lambda: len(list(tmp_path.glob("*.pcap"))) >= 2)

    files = sorted(p.name for p in tmp_path.glob("*.pcap"))
    assert files[0].endswith("-001.pcap")
    assert files[1].endswith("-002.pcap")

    status = pcap.status()
    assert status["running"] is True
    assert status["part"] >= 2


def test_stop_ends_the_session_without_further_rotation(tmp_path, monkeypatch):
    monkeypatch.setattr(pcap, "_ROTATE_BYTES", 20_000)
    monkeypatch.setattr(pcap, "_ROTATE_POLL_SECONDS", 0.05)

    pcap.start("eth0")
    assert _wait_until(lambda: len(list(tmp_path.glob("*.pcap"))) >= 2)

    pcap.stop()
    assert _wait_until(lambda: pcap.status()["running"] is False)

    count_at_stop = len(list(tmp_path.glob("*.pcap")))
    time.sleep(0.3)
    assert len(list(tmp_path.glob("*.pcap"))) == count_at_stop  # no further parts appear


def test_prune_oldest_deletes_lowest_mtime_files_first(tmp_path, monkeypatch):
    monkeypatch.setattr(pcap, "_MAX_TOTAL_BYTES", 1000)
    for i in range(5):
        path = tmp_path / f"lanpi-old{i}.pcap"
        path.write_bytes(b"x" * 300)
        mtime = time.time() - (5 - i) * 10
        os.utime(path, (mtime, mtime))

    pcap._prune_oldest(protect=None)

    remaining = sorted(p.name for p in tmp_path.glob("*.pcap"))
    # 1000-byte budget / 300 bytes per file -> the 3 newest survive
    assert remaining == ["lanpi-old2.pcap", "lanpi-old3.pcap", "lanpi-old4.pcap"]


def test_prune_oldest_never_deletes_the_protected_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pcap, "_MAX_TOTAL_BYTES", 500)
    old = tmp_path / "lanpi-old.pcap"
    old.write_bytes(b"x" * 2000)
    os.utime(old, (time.time() - 100, time.time() - 100))

    pcap._prune_oldest(protect="lanpi-old.pcap")

    assert old.exists()


def test_start_prunes_before_creating_the_new_session(tmp_path, monkeypatch):
    monkeypatch.setattr(pcap, "_MAX_TOTAL_BYTES", 100)
    old = tmp_path / "lanpi-old.pcap"
    old.write_bytes(b"x" * 500)
    os.utime(old, (time.time() - 100, time.time() - 100))

    result = pcap.start("eth0")

    assert result["ok"] is True
    assert not old.exists()
