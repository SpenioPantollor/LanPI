"""On-demand packet capture on the TEST PORT, via tcpdump -> .pcap file.

Mirrors backend/tools/ping.py's start/status/stop background-process
design: capture runs as a Popen'd tcpdump, stoppable early via SIGTERM
(tcpdump closes the pcap file cleanly on it, same as a graceful Ctrl+C)
or left running for a fixed duration, either way watched by a
background thread that clears run state once the process exits.
Capture files live in CAPTURE_DIR (repo-root/captures/, gitignored)
until explicitly deleted.
"""

from __future__ import annotations

import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

CAPTURE_DIR = Path(__file__).resolve().parent.parent.parent / "captures"
_TCPDUMP_CANDIDATES = ["/usr/bin/tcpdump", "/usr/sbin/tcpdump", "tcpdump"]
_MAX_DURATION_SECONDS = 3600

_lock = threading.Lock()
_state = {
    "running": False,
    "filename": None,
    "interface": None,
    "bpf_filter": None,
    "started_at": None,
    "duration_seconds": None,
    "process": None,
}


def _find_tcpdump() -> str | None:
    for candidate in _TCPDUMP_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _safe_filename(name: str) -> bool:
    """Bare filename only -- no path separators or traversal. Guards
    both the name generated here and any filename coming in from the
    API for list/download/delete lookups."""
    return bool(name) and "/" not in name and "\\" not in name and name not in (".", "..")


def _watch_loop(process: subprocess.Popen, filename: str, duration: int | None) -> None:
    try:
        process.wait(timeout=duration)
    except subprocess.TimeoutExpired:
        process.send_signal(signal.SIGTERM)
        process.wait()
    with _lock:
        if _state["filename"] == filename:
            _state["running"] = False
            _state["process"] = None


def start(interface: str, duration: int | None = None, bpf_filter: str | None = None) -> dict:
    if duration is not None and not (1 <= duration <= _MAX_DURATION_SECONDS):
        return {"ok": False, "message": f"duration must be between 1 and {_MAX_DURATION_SECONDS} seconds"}

    tcpdump = _find_tcpdump()
    if not tcpdump:
        return {"ok": False, "message": "tcpdump not available"}

    with _lock:
        old_process = _state.get("process")
        if old_process is not None and _state["running"]:
            # Don't block waiting for it -- its own watch thread notices
            # _state["filename"] no longer matches once the new capture
            # is set below and skips touching state, same "superseded"
            # pattern ping.py uses for a restart-while-running start().
            old_process.send_signal(signal.SIGTERM)

    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"lanpi-{time.strftime('%Y%m%dT%H%M%S')}.pcap"
    path = CAPTURE_DIR / filename

    args = [tcpdump, "-i", interface, "-U", "-nn", "-w", str(path)]
    if bpf_filter:
        args.append(bpf_filter)

    try:
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    except OSError as exc:
        return {"ok": False, "message": str(exc)}

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
                "interface": interface,
                "bpf_filter": bpf_filter,
                "started_at": time.time(),
                "duration_seconds": duration,
                "process": process,
            }
        )

    threading.Thread(target=_watch_loop, args=(process, filename, duration), daemon=True).start()
    return {"ok": True, "filename": filename}


def status() -> dict:
    with _lock:
        elapsed = round(time.time() - _state["started_at"], 1) if _state["started_at"] else None
        return {
            "running": _state["running"],
            "filename": _state["filename"],
            "interface": _state["interface"],
            "bpf_filter": _state["bpf_filter"],
            "duration_seconds": _state["duration_seconds"],
            "elapsed_seconds": elapsed,
        }


def stop() -> dict:
    with _lock:
        process = _state.get("process")
        if process is not None and _state["running"]:
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
