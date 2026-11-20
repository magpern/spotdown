"""Shared request dependencies of the v2 routers."""

from __future__ import annotations

from fastapi import Request

from ..config import Settings
from .errors import ApiError
from .service_key import ServiceKey
from .store import Store


def require_key(request: Request) -> None:
    """Second line behind ServiceKeyMiddleware: every v2 route checks the key itself too."""
    key: ServiceKey = request.app.state.service_key
    if not key.matches_authorization(request.headers.get("authorization")):
        raise ApiError(401, "unauthorized", "unauthorized")


def settings_of(request: Request) -> Settings:
    return request.app.state.settings


def store_of(request: Request) -> Store:
    return request.app.state.store
