import platform
import socket
import time

from fastapi import APIRouter

from backend.capture import dispatcher
from backend.version import get_version

router = APIRouter()

# time.monotonic(), not datetime.now(): the Pi has no battery-backed RTC,
# so the wall clock starts each boot at whatever it last saved on shutdown
# and only jumps to the real time once NTP syncs, which happens after this
# module (and the wall clock it would've stamped _START_TIME with) loads.
# A wall-clock-based elapsed time would include that jump -- e.g. boot at
# a stale 23:48, NTP corrects to 16:37 a few minutes later, elapsed
# computed as "16:37 minus 23:48" comes out to ~16.8 hours instead of the
# real ~6 minutes. Monotonic time is immune to clock steps by design.
_START_TIME = time.monotonic()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/status")
def status() -> dict:
    uptime_seconds = time.monotonic() - _START_TIME
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "lanpi_version": get_version(),
        "backend_uptime_seconds": int(uptime_seconds),
        "capture_dispatcher": dispatcher.get_health(),
    }
