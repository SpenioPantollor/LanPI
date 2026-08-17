"""Hardware/system info: CPU load & temperature, memory, disk, Pi model.

Reads directly from /proc and /sys -- no extra dependency (no psutil),
Linux-only by design (this only ever runs on the Pi).
"""

from __future__ import annotations

import os
import shutil
import time


def _read_file(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def get_model() -> str | None:
    raw = _read_file("/proc/device-tree/model")
    if raw is None:
        return None
    return raw.rstrip("\x00").strip() or None


def get_cpu_temp_celsius() -> float | None:
    raw = _read_file("/sys/class/thermal/thermal_zone0/temp")
    if raw is None:
        return None
    try:
        return round(int(raw.strip()) / 1000, 1)
    except ValueError:
        return None


def get_load_average() -> dict:
    try:
        one, five, fifteen = os.getloadavg()
    except OSError:
        return {"1min": None, "5min": None, "15min": None}
    return {"1min": round(one, 2), "5min": round(five, 2), "15min": round(fifteen, 2)}


def _read_proc_stat_cpu_line() -> list[int] | None:
    content = _read_file("/proc/stat")
    if not content:
        return None
    first_line = content.splitlines()[0]
    if not first_line.startswith("cpu "):
        return None
    try:
        return [int(x) for x in first_line.split()[1:]]
    except ValueError:
        return None


def get_cpu_percent(sample_seconds: float = 0.15) -> float | None:
    """Instantaneous CPU utilization, sampled over a short window."""
    first = _read_proc_stat_cpu_line()
    if first is None:
        return None
    time.sleep(sample_seconds)
    second = _read_proc_stat_cpu_line()
    if second is None or len(second) != len(first):
        return None

    deltas = [b - a for a, b in zip(first, second)]
    total = sum(deltas)
    if total <= 0:
        return None
    idle = deltas[3] + (deltas[4] if len(deltas) > 4 else 0)  # idle + iowait
    busy = total - idle
    return round(100 * busy / total, 1)


def get_memory() -> dict:
    content = _read_file("/proc/meminfo")
    if not content:
        return {"total_bytes": None, "used_bytes": None, "percent": None}

    values = {}
    for line in content.splitlines():
        key, _, rest = line.partition(":")
        rest = rest.strip()
        if rest.endswith("kB"):
            try:
                values[key] = int(rest[:-2].strip()) * 1024
            except ValueError:
                continue

    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if total is None or available is None:
        return {"total_bytes": None, "used_bytes": None, "percent": None}

    used = total - available
    percent = round(100 * used / total, 1) if total else None
    return {"total_bytes": total, "used_bytes": used, "percent": percent}


def get_disk() -> dict:
    try:
        usage = shutil.disk_usage("/")
    except OSError:
        return {"total_bytes": None, "used_bytes": None, "percent": None}
    percent = round(100 * usage.used / usage.total, 1) if usage.total else None
    return {"total_bytes": usage.total, "used_bytes": usage.used, "percent": percent}


def get_uptime_seconds() -> float | None:
    raw = _read_file("/proc/uptime")
    if raw is None:
        return None
    try:
        return float(raw.split()[0])
    except (ValueError, IndexError):
        return None


def get_system_info() -> dict:
    return {
        "model": get_model(),
        "cpu_temp_celsius": get_cpu_temp_celsius(),
        "cpu_percent": get_cpu_percent(),
        "load_average": get_load_average(),
        "memory": get_memory(),
        "disk": get_disk(),
        "system_uptime_seconds": get_uptime_seconds(),
    }
