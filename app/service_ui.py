"""The web UI in service mode (ADR 0004): served only with ``SPOTDOWN_UI=on``.

The page and a fixed list of its own scripts and stylesheet, nothing else: no ``/files``, no
``/api``, no ``/login``, no API docs and no data. These routes carry no service key; the page holds
no data of its own and analyses only through ``/v2``, with the key the user types in, so every
service-mode guarantee of ``/v2`` (the key, no audio back, no CORS) is unchanged. A strict
Content-Security-Policy keeps the key, which lives in the page's memory, away from any injected
script or framing page.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse

STATIC_DIR = Path(__file__).parent / "static"
PAGE = "service.html"

# The service page's files (service.html loads exactly these). The standalone-only scripts (ui.js,
# upload.js) are not among them: they call routes service mode never has.
SERVICE_UI_ASSETS = {
    "ui.css": "text/css",
    "ui_common.js": "text/javascript",
    "analysis_model.js": "text/javascript",
    "analysis_timeline.js": "text/javascript",
    "analysis_view.js": "text/javascript",
    "waveform_model.js": "text/javascript",
    "waveform_view.js": "text/javascript",
    "waveform_player.js": "text/javascript",
    "service_ui.js": "text/javascript",
}

# Scripts and styles from this origin only (no inline script); requests to this origin only; no
# framing, no forms, no <base>. Inline style attributes are used by the analysis view.
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; form-action 'none'; frame-ancestors 'none'; base-uri 'none'"
)
HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

router = APIRouter()


@router.get("/", include_in_schema=False)
def service_page() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / PAGE).read_text(encoding="utf-8"), headers=HEADERS)


# Matches every single-segment path, so it is included after /healthz (app.main); every /v2 path is
# refused by the service-key middleware before routing.
@router.get("/{name}", include_in_schema=False)
def service_asset(name: str) -> FileResponse:
    """A fixed list, so no path is ever built from input."""
    if name not in SERVICE_UI_ASSETS:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(STATIC_DIR / name, media_type=SERVICE_UI_ASSETS[name], headers=HEADERS)
