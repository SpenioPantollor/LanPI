from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from backend.api.routes import TEST_PORT_INTERFACE, router as api_router
from backend.discovery import cdp, lldp, mndp

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="LanPi", version="0.1.0")


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


@app.exception_handler(StarletteHTTPException)
async def captive_portal_fallback(request: Request, exc: StarletteHTTPException):
    # OS captive-portal probes (iOS hotspot-detect.html, Android
    # generate_204, Windows connecttest.txt, ...) hit arbitrary paths
    # that don't exist here. Send anything unmatched to the dashboard
    # instead of a bare 404, so joining the fallback AP opens LanPi
    # automatically instead of requiring the user to type an address.
    # Excludes /api/* so a real API 404 (e.g. a missing capture file)
    # comes back as an actual 404, not a redirect masking it.
    if exc.status_code == 404 and not request.url.path.startswith("/api/"):
        return RedirectResponse(url="/")
    # Registering a handler for StarletteHTTPException replaces FastAPI's
    # own default one entirely -- re-raising here doesn't fall through to
    # it, it just becomes an unhandled 500 (found via capture_download's
    # 404). Build the same {"detail": ...} response the default handler
    # would, for every other status code this exception carries.
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)
