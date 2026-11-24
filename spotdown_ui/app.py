"""The UI image's app (ADR 0005 Decision 7): ``uvicorn --factory spotdown_ui.app:create_app``.

Everything is checked at construction, so a bad setting stops the process before it listens:
``SPOTDOWN_BACKEND_URL``, the key file, the ``Host`` allowlist and the public origin; the access
token is read or generated here too (ADR 0003: no lifespan is needed to authenticate). No CORS, no
API docs. The middleware order, outermost first: the security headers, the session (``UiAuth``),
then the routes.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api_policy import error_body
from .auth import UiAuth
from .backend import BackendClient
from .backend_key import load_backend_key
from .headers import SecurityHeaders
from .routes import register
from .settings import UiSettings, parse_settings
from .token_store import FINAL_NAME, TokenSource

_HTTP_CODES = {404: ("not_found", "not found"), 405: ("method_not_allowed", "method not allowed")}


def _install_errors(app: FastAPI) -> None:
    async def _http(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, StarletteHTTPException)
        code, message = _HTTP_CODES.get(exc.status_code, ("http_error", "request refused"))
        response = JSONResponse(error_body(code, message), status_code=exc.status_code)
        if exc.headers:  # Allow on a 405
            response.headers.update(exc.headers)
        return response

    app.add_exception_handler(StarletteHTTPException, _http)


def build_app(
    settings: UiSettings, transport: Optional[httpx.AsyncBaseTransport] = None
) -> FastAPI:
    key = load_backend_key(settings.backend_key_file)  # fails closed
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    owner = settings.access_token_file
    tokens = TokenSource(owner or settings.data_dir / FINAL_NAME, owner=owner is not None)
    backend = BackendClient(settings.backend, key, settings.backend_timeout_s, transport)

    app = FastAPI(title="spotdown-ui", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.backend = backend
    _install_errors(app)
    register(app, settings, backend)
    app.add_middleware(
        UiAuth,
        tokens=tokens,
        allowed_hosts=settings.allowed_hosts,
        public_origin=settings.public_origin,
    )
    app.add_middleware(SecurityHeaders)
    return app


def create_app(
    env: Optional[Mapping[str, str]] = None, transport: Optional[httpx.AsyncBaseTransport] = None
) -> FastAPI:
    return build_app(parse_settings(os.environ if env is None else env), transport)
