from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Callable, Optional

from fastapi import FastAPI

from .config import Settings, get_settings
from .queue import get_redis


def _healthz() -> dict:
    try:
        get_redis().ping()
        redis_ok = True
    except Exception:  # noqa: BLE001
        redis_ok = False
    return {"ok": True, "redis": redis_ok}


def create_standalone_app(settings: Settings) -> FastAPI:
    """The web UI and the v1 API, all behind the token session (ADR 0003). Settings, the data
    directories and the token are handled here, at construction, never in the lifespan: an app
    run without a lifespan authenticates the same way. No CORS."""
    from . import db, upload_route
    from .exposure_policy import parse_allowed_hosts, parse_public_origin
    from .standalone import router
    from .standalone_auth import StandaloneAuth
    from .token_store import TokenSource

    allowed_hosts = parse_allowed_hosts(settings.ui_allowed_hosts)  # raises on a bad entry
    public_origin = parse_public_origin(settings.ui_public_origin)  # raises if not exact
    settings.ensure_dirs()
    owner_file = settings.standalone_access_token_file
    tokens = TokenSource(owner_file or settings.standalone_token_path, owner=owner_file is not None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_db()
        yield

    app = FastAPI(title="spotdown", version="0.1.0", lifespan=lifespan)
    app.get("/healthz")(_healthz)
    app.include_router(router)
    upload_route.register(app, settings)  # local files (ADR 0002): POSIX and the UI only
    app.add_middleware(
        StandaloneAuth, tokens=tokens, allowed_hosts=allowed_hosts, public_origin=public_origin
    )
    return app


def create_service_app(
    settings: Settings, dispatch: Optional[Callable[[str], None]] = None
) -> FastAPI:
    """Service mode: ``/v2`` behind the service key, and ``/healthz``; the static UI page only with
    ``SPOTDOWN_UI=on`` (ADR 0004). Never ``/files``, ``/api/v1``, the standalone Spotify routes,
    CORS or API docs (Spotify links resolve through ``/v2/spotify/resolve``)."""
    from .service import (
        access_log,
        cache_routes,
        errors,
        match_routes,
        routes,
        spotify_routes,
        url_policy,
    )
    from .service.dispatch import rq_dispatcher
    from .service.service_key import ServiceKeyMiddleware, load_service_keys
    from .service.store import Store
    from .youtube.ytdlp_client import YtDlpClient

    # Fails closed without classbeat's key, and on a bad or duplicate UI key (ADR 0005).
    key = load_service_keys(settings.service_key_file, settings.ui_service_key_file)
    url_policy.parse_allowlist(settings.url_allowed_hosts)  # fails on a bad entry
    settings.ensure_dirs()
    access_log.install()  # no query string (a lookup's url) in the access log
    app = FastAPI(
        title="spotdown", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.settings = settings
    app.state.service_key = key
    app.state.store = Store(settings.service_db_path, settings.global_reference_ttl_s)
    app.state.dispatch = dispatch or rq_dispatcher(settings)
    app.state.youtube = YtDlpClient()  # used only when ALLOW_YTDLP_SOURCING=true
    app.state.spotify = spotify_routes.client_from_settings(settings)  # None: spotify_disabled
    errors.install_handlers(app)
    app.get("/healthz")(_healthz)
    app.include_router(routes.router)
    app.include_router(cache_routes.router)
    app.include_router(match_routes.router)
    app.include_router(spotify_routes.router)
    if settings.ui_enabled:  # SPOTDOWN_UI=on only; last, after every fixed route
        from .service_ui import router as ui_router

        app.include_router(ui_router)
    app.add_middleware(ServiceKeyMiddleware, key=key)
    return app


def create_app(
    settings: Optional[Settings] = None, dispatch: Optional[Callable[[str], None]] = None
) -> FastAPI:
    settings = settings or get_settings()
    if settings.service_mode:
        return create_service_app(settings, dispatch)
    return create_standalone_app(settings)


app = create_app()
