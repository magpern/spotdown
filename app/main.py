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
    from fastapi.middleware.cors import CORSMiddleware

    from . import db
    from .standalone import router

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.ensure_dirs()
        db.init_db()
        yield

    app = FastAPI(title="spotdown", version="0.1.0", lifespan=lifespan)
    # Dev convenience: allow the page to call the API when it's opened cross-origin
    # (e.g. a local file:// editor preview hitting http://localhost:8000). Tighten
    # allow_origins for any real deployment.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.get("/healthz")(_healthz)
    app.include_router(router)
    return app


def create_service_app(
    settings: Settings, dispatch: Optional[Callable[[str], None]] = None
) -> FastAPI:
    """Service mode: ``/v2`` behind the service key, and ``/healthz``. No UI, no ``/files``,
    no ``/api/v1``, no Spotify resolution, no CORS and no API docs."""
    from .service import cache_routes, errors, match_routes, routes, url_policy
    from .service.dispatch import rq_dispatcher
    from .service.service_key import ServiceKeyMiddleware, load_service_key
    from .service.store import Store
    from .youtube.ytdlp_client import YtDlpClient

    key = load_service_key(settings.service_key_file)  # fails closed without a key
    url_policy.parse_allowlist(settings.url_allowed_hosts)  # fails on a bad entry
    settings.ensure_dirs()
    app = FastAPI(
        title="spotdown", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.settings = settings
    app.state.service_key = key
    app.state.store = Store(settings.service_db_path, settings.global_reference_ttl_s)
    app.state.dispatch = dispatch or rq_dispatcher(settings)
    app.state.youtube = YtDlpClient()  # used only when ALLOW_YTDLP_SOURCING=true
    errors.install_handlers(app)
    app.get("/healthz")(_healthz)
    app.include_router(routes.router)
    app.include_router(cache_routes.router)
    app.include_router(match_routes.router)
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
