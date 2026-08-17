"""ICMP ping via the system `ping` command.

Runs as a background process so the UI can show live results and stop
it early, rather than blocking on a fixed count. `count` is optional:
given, ping stops itself after that many packets (still stoppable
early); omitted, it runs continuously until stopped.

`received` and `replies` update live, one per reply line as they
arrive. `transmitted`/`packet_loss_percent` are only known once ping's
own summary line has been printed -- which happens when it finishes
its count, or when stopped via SIGINT (not available while running).
"""

from __future__ import annotations

import re
import shutil
import signal
import subprocess
import threading

_PING_CANDIDATES = ["/bin/ping", "/usr/bin/ping", "ping"]
_REPLY_RE = re.compile(r"icmp_seq=(\d+) ttl=(\d+) time=([\d.]+) ms")
_SUMMARY_RE = re.compile(
    r"(\d+) packets transmitted, (\d+) received,.*?([\d.]+)% packet loss"
)
_MAX_STORED_REPLIES = 200

_lock = threading.Lock()
_state = {
    "running": False,
    "host": None,
    "process": None,
    "replies": [],
    "received": 0,
    "transmitted": None,
    "packet_loss_percent": None,
}


def _find_ping() -> str | None:
    for candidate in _PING_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _reader_loop(process: subprocess.Popen, host: str) -> None:
    output_lines = []
    for line in process.stdout:
        output_lines.append(line)
        match = _REPLY_RE.search(line)
        if not match:
            continue
        with _lock:
            if _state["host"] != host:
                return  # superseded by a newer ping session
            _state["received"] += 1
            _state["replies"].append(
                {
                    "seq": int(match.group(1)),
                    "ttl": int(match.group(2)),
                    "time_ms": float(match.group(3)),
                }
            )
            if len(_state["replies"]) > _MAX_STORED_REPLIES:
                _state["replies"] = _state["replies"][-_MAX_STORED_REPLIES:]

    process.wait()

    summary_match = _SUMMARY_RE.search("".join(output_lines))
    with _lock:
        if _state["host"] != host:
            return
        _state["running"] = False
        if summary_match:
            _state["transmitted"] = int(summary_match.group(1))
            _state["received"] = int(summary_match.group(2))
            _state["packet_loss_percent"] = float(summary_match.group(3))


def start(host: str, count: int | None = None) -> dict:
    host = (host or "").strip()
    if not host:
        return {"ok": False, "message": "host is required"}
    if host.startswith("-"):
        return {"ok": False, "message": "invalid host"}
    if count is not None and count < 1:
        return {"ok": False, "message": "count must be at least 1"}

    ping_bin = _find_ping()
    if not ping_bin:
        return {"ok": False, "message": "ping not available"}

    args = [ping_bin]
    if count:
        args += ["-c", str(count)]
    args.append(host)

    with _lock:
        old_process = _state.get("process")
        if old_process is not None and _state["running"]:
            old_process.send_signal(signal.SIGINT)

        try:
            process = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
        except OSError as exc:
            return {"ok": False, "message": str(exc)}

        _state["running"] = True
        _state["host"] = host
        _state["process"] = process
        _state["replies"] = []
        _state["received"] = 0
        _state["transmitted"] = None
        _state["packet_loss_percent"] = None

    threading.Thread(target=_reader_loop, args=(process, host), daemon=True).start()
    return {"ok": True}


def status() -> dict:
    with _lock:
        return {
            "running": _state["running"],
            "host": _state["host"],
            "transmitted": _state["transmitted"],
            "received": _state["received"],
            "packet_loss_percent": _state["packet_loss_percent"],
            "replies": _state["replies"][-20:],
        }


def stop() -> dict:
    with _lock:
        process = _state.get("process")
        if process is not None and _state["running"]:
            # SIGINT (not SIGTERM) so ping prints its usual summary line
            # before exiting -- the reader loop picks that up and fills
            # in transmitted/packet_loss_percent, same as a natural stop.
            process.send_signal(signal.SIGINT)
            return {"ok": True, "message": "ping stopping"}
        return {"ok": True, "message": "no ping running"}
