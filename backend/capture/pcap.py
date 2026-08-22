"""On-demand packet capture on the TEST PORT, via tcpdump -> .pcap file(s).

Mirrors backend/tools/ping.py's start/status/stop background-process
design: capture runs as a Popen'd tcpdump, stoppable early via SIGTERM
(tcpdump closes the pcap file cleanly on it, same as a graceful Ctrl+C)
or left running for a fixed duration. Capture files live in
CAPTURE_DIR (repo-root/captures/, gitignored) until explicitly deleted.

Storage limits (v0.2.3 Foundation): a single unattended capture on a
busy network could otherwise fill the Pi's SD card. Two independent
caps:

  - Per-file rotation at _ROTATE_BYTES (~100MB): a background thread
    polls the active file's size once a second and, on crossing the
    threshold, cleanly stops the current tcpdump (SIGTERM, same as a
    manual stop) and immediately starts a new one -- a "session"
    (one start() call) can span several files, named
    lanpi-<started>-<part>.pcap, all shown as separate rows by
    list_captures() like any other file. This is deliberately *not*
    tcpdump's own -C flag: -C appends a bare number to the filename
    with no .pcap extension (foo.pcap -> foo.pcap1), which breaks
    double-click-to-open-in-Wireshark; a clean stop/restart keeps
    every part a properly named, independently valid .pcap. Trade-off:
    a poll interval means a file can overshoot ~100MB by up to a
    second's worth of traffic, and there's a brief (sub-second) gap
    in capture during the restart -- both fine for a diagnostic tool,
    not acceptable for forensic capture.
  - Total storage cap at _MAX_TOTAL_BYTES (~1GB): _prune_oldest()
    deletes the oldest .pcap files (by mtime) whenever the directory
    total exceeds this, run before starting a new session and after
    every rotation/stop. Never deletes the file a capture is actively
    writing to right now (the `protect` argument) -- only finished
    files are ever pruned, so a running capture is never corrupted or
    silently orphaned by its own cleanup pass.
"""

from __future__ import annotations

import signal
import subprocess
import threading
import time
from pathlib import Path

from backend import shell

CAPTURE_DIR = Path(__file__).resolve().parent.parent.parent / "captures"
_TCPDUMP_CANDIDATES = ["/usr/bin/tcpdump", "/usr/sbin/tcpdump", "tcpdump"]
_MAX_DURATION_SECONDS = 3600
_ROTATE_BYTES = 100_000_000
_MAX_TOTAL_BYTES = 1_000_000_000
_ROTATE_POLL_SECONDS = 1.0

_lock = threading.Lock()
_state = {
    "running": False,
    "filename": None,  # currently active file (most recent part)
    "part": None,
    "interface": None,
    "bpf_filter": None,
    "started_at": None,  # session start, constant across rotations
    "duration_seconds": None,  # total session budget, constant across rotations
    "process": None,
    "session_id": 0,  # bumped on every start() so a superseded session's thread exits cleanly
    "_stop_requested": False,
}


def _find_tcpdump() -> str | None:
    return shell.find_binary(_TCPDUMP_CANDIDATES)


def _safe_filename(name: str) -> bool:
    """Bare filename only -- no path separators or traversal. Guards
    both the name generated here and any filename coming in from the
    API for list/download/delete lookups."""
    return bool(name) and "/" not in name and "\\" not in name and name not in (".", "..")


def _spawn(tcpdump: str, interface: str, bpf_filter: str | None, path: Path) -> subprocess.Popen | None:
    args = [tcpdump, "-i", interface, "-U", "-nn", "-w", str(path)]
    if bpf_filter:
        args.append(bpf_filter)
    try:
        return subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except OSError:
        return None


def _prune_oldest(protect: str | None) -> None:
    if not CAPTURE_DIR.exists():
        return
    try:
        files = sorted(CAPTURE_DIR.glob("*.pcap"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return

    total = 0
    for path in files:
        try:
            total += path.stat().st_size
        except OSError:
            pass

    for path in files:
        if total <= _MAX_TOTAL_BYTES:
            break
        if path.name == protect:
            continue
        try:
            size = path.stat().st_size
            path.unlink()
            total -= size
        except OSError:
            pass


def _session_loop(
    tcpdump: str,
    interface: str,
    bpf_filter: str | None,
    session_start: str,
    duration: int | None,
    process: subprocess.Popen,
    path: Path,
    part: int,
    session_id: int,
) -> None:
    deadline = time.monotonic() + duration if duration else None

    while True:
        stop_requested = timed_out = exited = False
        while True:
            with _lock:
                if _state["session_id"] != session_id:
                    if process.poll() is None:
                        process.send_signal(signal.SIGTERM)
                    process.wait()
                    return
                stop_requested = _state["_stop_requested"]
            exited = process.poll() is not None
            timed_out = deadline is not None and time.monotonic() >= deadline
            try:
                oversize = path.stat().st_size >= _ROTATE_BYTES
            except OSError:
                oversize = False
            if exited or stop_requested or timed_out or oversize:
                break
            time.sleep(_ROTATE_POLL_SECONDS)

        finishing = stop_requested or timed_out or exited
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
        process.wait()
        _prune_oldest(protect=None)

        with _lock:
            if _state["session_id"] != session_id:
                return
            if finishing:
                _state["running"] = False
                _state["process"] = None
                _state["started_at"] = None
                return

        part += 1
        filename = f"lanpi-{session_start}-{part:03d}.pcap"
        path = CAPTURE_DIR / filename
        process = _spawn(tcpdump, interface, bpf_filter, path)

        with _lock:
            if _state["session_id"] != session_id:
                if process is not None:
                    if process.poll() is None:
                        process.send_signal(signal.SIGTERM)
                    process.wait()
                return
            if process is None:
                _state["running"] = False
                _state["process"] = None
                _state["started_at"] = None
                return
            _state.update({"filename": filename, "part": part, "process": process})


def start(interface: str, duration: int | None = None, bpf_filter: str | None = None) -> dict:
    if duration is not None and not (1 <= duration <= _MAX_DURATION_SECONDS):
        return {"ok": False, "message": f"duration must be between 1 and {_MAX_DURATION_SECONDS} seconds"}

    tcpdump = _find_tcpdump()
    if not tcpdump:
        return {"ok": False, "message": "tcpdump not available"}

    with _lock:
        old_process = _state.get("process")
        if old_process is not None and _state["running"]:
            # Don't block waiting for it -- its own session loop notices
            # session_id no longer matches once the new session is
            # committed below and exits without touching state, same
            # "superseded" pattern used by mtr.py/ip_scanner.py.
            old_process.send_signal(signal.SIGTERM)
        session_id = _state["session_id"] + 1

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    _prune_oldest(protect=None)

    session_start = time.strftime("%Y%m%dT%H%M%S")
    filename = f"lanpi-{session_start}-001.pcap"
    path = CAPTURE_DIR / filename

    process = _spawn(tcpdump, interface, bpf_filter, path)
    if process is None:
        return {"ok": False, "message": "failed to start tcpdump"}

    # A bad BPF filter or interface makes tcpdump exit almost
    # immediately with a parse error -- catch that synchronously so the
    # caller gets a real error instead of a falsely "running" state.
    time.sleep(0.3)
    if process.poll() is not None:
        stderr = (process.stderr.read() if process.stderr else "").strip()
        return {"ok": False, "message": stderr or "tcpdump exited immediately"}

    with _lock:
        _state.update(
            {
                "running": True,
                "filename": filename,
                "part": 1,
                "interface": interface,
                "bpf_filter": bpf_filter,
                "started_at": time.time(),
                "duration_seconds": duration,
                "process": process,
                "session_id": session_id,
                "_stop_requested": False,
            }
        )

    threading.Thread(
        target=_session_loop,
        args=(tcpdump, interface, bpf_filter, session_start, duration, process, path, 1, session_id),
        daemon=True,
    ).start()
    return {"ok": True, "filename": filename}


def status() -> dict:
    with _lock:
        elapsed = round(time.time() - _state["started_at"], 1) if _state["started_at"] else None
        return {
            "running": _state["running"],
            "filename": _state["filename"],
            "part": _state["part"],
            "interface": _state["interface"],
            "bpf_filter": _state["bpf_filter"],
            "duration_seconds": _state["duration_seconds"],
            "elapsed_seconds": elapsed,
        }


def stop() -> dict:
    with _lock:
        process = _state.get("process")
        if process is not None and _state["running"]:
            _state["_stop_requested"] = True
            process.send_signal(signal.SIGTERM)
            return {"ok": True, "message": "capture stopping"}
        return {"ok": True, "message": "no capture running"}


def list_captures() -> list[dict]:
    if not CAPTURE_DIR.exists():
        return []
    files = []
    for path in sorted(CAPTURE_DIR.glob("*.pcap"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        files.append({"filename": path.name, "size_bytes": stat.st_size, "modified": int(stat.st_mtime)})
    return files


def get_capture_path(filename: str) -> Path | None:
    if not _safe_filename(filename):
        return None
    path = CAPTURE_DIR / filename
    return path if path.is_file() else None


def delete_capture(filename: str) -> dict:
    if not _safe_filename(filename):
        return {"ok": False, "message": "invalid filename"}
    path = CAPTURE_DIR / filename
    try:
        path.unlink()
    except FileNotFoundError:
        return {"ok": False, "message": "capture not found"}
    except OSError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "message": "deleted"}
