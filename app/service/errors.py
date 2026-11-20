"""The v2 error envelope: ``{"error": {"code", "message"}}`` (docs/contract-v2.md)."""

from __future__ import annotations

import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")


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
        # FastAPI's default 422 echoes the input, which could carry a URL's query string.
        assert isinstance(exc, RequestValidationError)
        locs = {".".join(str(p) for p in e.get("loc", ())[1:]) for e in exc.errors()}
        fields = sorted(f for f in locs if _FIELD.match(f)) or ["body"]
        return error_response(422, "invalid_request", "invalid fields: " + ", ".join(fields))

    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(RequestValidationError, _validation)
