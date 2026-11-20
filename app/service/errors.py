"""The v2 error envelope: ``{"error": {"code", "message"}}`` (docs/contract-v2.md)."""

from __future__ import annotations

import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")
# Starlette's own errors (an unknown route or method) in the envelope, with a fixed message.
_HTTP_CODES = {404: ("not_found", "not found"), 405: ("method_not_allowed", "method not allowed")}


class ApiError(Exception):
    """A request error with a stable code. The message never echoes request input."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def install_handlers(app: FastAPI) -> None:
    async def _api_error(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, ApiError)
        return error_response(exc.status, exc.code, exc.message)

    async def _validation(_: Request, exc: Exception) -> JSONResponse:
        # FastAPI's default 422 echoes the input, which could carry a URL's query string. An
        # unknown field's name is input too (caller-chosen), so it is reported as "body".
        assert isinstance(exc, RequestValidationError)
        locs = {
            ".".join(str(p) for p in e.get("loc", ())[1:])
            for e in exc.errors()
            if e.get("type") != "extra_forbidden"
        }
        fields = sorted(f for f in locs if _FIELD.fullmatch(f)) or ["body"]
        return error_response(422, "invalid_request", "invalid fields: " + ", ".join(fields))

    async def _http(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, StarletteHTTPException)
        code, message = _HTTP_CODES.get(exc.status_code, ("http_error", "request refused"))
        response = error_response(exc.status_code, code, message)
        if exc.headers:  # e.g. Allow on a 405
            response.headers.update(exc.headers)
        return response

    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(StarletteHTTPException, _http)
