from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from backend.api.routes import TEST_PORT_INTERFACE, router as api_router
from backend.capture import dhcp_monitor, ip_conflict, modbus_traffic, traffic_stats
from backend.discovery import cdp, lldp, mndp
from backend.network import link_history
from backend.version import get_version

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="LanPi", version=get_version())


class NoCacheMiddleware(BaseHTTPMiddleware):
    """Without this, StaticFiles sends no Cache-Control header at all,
    so browsers fall back to heuristic caching and can keep serving a
    pre-deploy index.html/app.js indefinitely -- silently, with no
    error, just stale event handlers not being attached (looked like a
    dead "Test" button that fell through to a native form submit
    instead of the JS handler). no-cache (not no-store) still lets
    StaticFiles' existing ETag/Last-Modified answer normal conditional
    GETs with a cheap 304, it just forces that revalidation to happen
    on every load instead of trusting a local copy blindly."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if not request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-cache"
        return response


app.add_middleware(NoCacheMiddleware)
app.include_router(api_router, prefix="/api")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


@app.on_event("startup")
def _start_background_listeners() -> None:
    lldp.start_listener(TEST_PORT_INTERFACE)
    cdp.start_listener(TEST_PORT_INTERFACE)
    mndp.start_listener(TEST_PORT_INTERFACE)
    traffic_stats.start_listener(TEST_PORT_INTERFACE)
    modbus_traffic.start_listener(TEST_PORT_INTERFACE)
    link_history.start_listener(TEST_PORT_INTERFACE)
    ip_conflict.start_listener(TEST_PORT_INTERFACE)
    dhcp_monitor.start_listener(TEST_PORT_INTERFACE)
