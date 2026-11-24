"""The backend (ADR 0005 Decision 2): ``/v2/...`` behind a caller key, and ``/healthz``. Every other
path is 404. No mode, no CORS, no API docs, no static files and no token session.

``uvicorn app.main:app`` builds the app on first access of ``app`` (PEP 562), so importing this
module needs no key file; ``create_app`` fails closed without one.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from fastapi import FastAPI

from .config import Settings, get_settings
from .queue import get_redis

log = logging.getLogger("spotdown")


def _healthz() -> dict:
    try:
        get_redis().ping()
        redis_ok = True
    except Exception:  # noqa: BLE001
        redis_ok = False
    return {"ok": True, "redis": redis_ok}


def warn_deprecated(settings: Settings) -> None:
    """One warning naming the deprecated settings that are set, never their values."""
    names = settings.deprecated_set()
    if names:
        log.warning(
            "deprecated settings are ignored and will be removed in the next release: %s",
            ", ".join(names),
        )


def create_app(
    settings: Optional[Settings] = None, dispatch: Optional[Callable[[str], None]] = None
) -> FastAPI:
    from .service import (
        access_log,
        audio_routes,
        cache_routes,
        errors,
        match_routes,
        routes,
        spotify_routes,
        url_policy,
    )
    from .service.dispatch import rq_dispatcher
    from .service.service_key import ServiceKeyMiddleware, load_service_keys
    from .service.spotify_client import resolver_from_settings
    from .service.store import Store
    from .youtube.ytdlp_client import YtDlpClient

    settings = settings or get_settings()
    # Fails closed without classbeat's key, and on a bad or duplicate UI key (ADR 0005).
    key = load_service_keys(settings.service_key_file, settings.ui_service_key_file)
    url_policy.parse_allowlist(settings.url_allowed_hosts)  # fails on a bad entry
    warn_deprecated(settings)
    settings.ensure_dirs()
    access_log.install()  # no query string (a lookup's url) in the access log
    app = FastAPI(
        title="spotdown", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.settings = settings
    app.state.service_key = key
    app.state.store = Store(settings.service_db_path, settings.global_reference_ttl_s)
    app.state.dispatch = dispatch or rq_dispatcher(settings)
    app.state.youtube = YtDlpClient()  # unused with ALLOW_YTDLP_SOURCING=false
    # The Web API with both Spotify settings, the public pages with neither; one alone fails.
    app.state.spotify = resolver_from_settings(
        settings.spotify_client_id, settings.spotify_client_secret_file
    )
    errors.install_handlers(app)
    app.get("/healthz")(_healthz)
    app.include_router(routes.router)
    app.include_router(cache_routes.router)
    app.include_router(match_routes.router)
    app.include_router(spotify_routes.router)
    app.include_router(audio_routes.router)  # the one route that returns audio (ADR 0005)
    app.add_middleware(ServiceKeyMiddleware, key=key)
    return app


def __getattr__(name: str) -> FastAPI:
    """``app.main:app`` (the image's command): built once, on first access."""
    if name != "app":
        raise AttributeError(name)
    built = create_app()
    globals()["app"] = built
    return built
