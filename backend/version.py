"""Single source of truth for the app version -- see VERSION at the
repo root. Used by FastAPI's own metadata and /api/status so a git tag
like v0.2.3 and what the app reports agree, instead of separate
hardcoded strings drifting apart (found and fixed once already: main.py
and routes.py each had their own copy, one stuck on an old release).
"""

from __future__ import annotations

from pathlib import Path

_VERSION_PATH = Path(__file__).resolve().parent.parent / "VERSION"
_FALLBACK = "unknown"


def get_version() -> str:
    try:
        return _VERSION_PATH.read_text().strip() or _FALLBACK
    except OSError:
        return _FALLBACK
