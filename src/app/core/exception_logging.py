import json
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

logger = structlog.get_logger(__name__)

_MAX_LOGGED_BODY_LENGTH = 4000


async def _serialize_request_body(request: Request) -> str | None:
    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return None

    content_type = (request.headers.get("content-type") or "").lower()
    try:
        raw_body = await request.body()
    except Exception as exc:  # pragma: no cover - defensive logging helper
        return f"<failed to read request body: {exc}>"

    if not raw_body:
        return None

    if "application/json" in content_type:
        try:
            parsed = json.loads(raw_body.decode("utf-8", errors="replace"))
            serialized = json.dumps(parsed, ensure_ascii=False)
        except Exception:
            serialized = raw_body.decode("utf-8", errors="replace")
    elif (
        "application/x-www-form-urlencoded" in content_type
        or "text/plain" in content_type
        or "application/xml" in content_type
        or "text/xml" in content_type
    ):
        serialized = raw_body.decode("utf-8", errors="replace")
    elif "multipart/form-data" in content_type:
        serialized = "<multipart form-data omitted>"
    else:
        serialized = f"<{content_type or 'binary'} body omitted>"

    if len(serialized) > _MAX_LOGGED_BODY_LENGTH:
        return f"{serialized[:_MAX_LOGGED_BODY_LENGTH]}...(truncated)"
    return serialized


def _build_request_log_context(request: Request) -> dict[str, Any]:
    return {
        "path_params": dict(request.path_params),
        "query_params": dict(request.query_params),
    }


def register_exception_logging(application: FastAPI) -> None:
    @application.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ):
        request.state.error_logged = True
        logger.warning(
            "Request validation failed",
            request_body=await _serialize_request_body(request),
            validation_errors=exc.errors(),
            **_build_request_log_context(request),
        )
        return await request_validation_exception_handler(request, exc)

    @application.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request,
        exc: StarletteHTTPException,
    ):
        request.state.error_logged = True
        logger.warning(
            "HTTP request failed",
            response_detail=exc.detail,
            status_code=exc.status_code,
            request_body=await _serialize_request_body(request),
            **_build_request_log_context(request),
        )
        return await http_exception_handler(request, exc)

    @application.exception_handler(Exception)
    async def handle_unexpected_exception(
        request: Request,
        exc: Exception,
    ):
        request.state.error_logged = True
        logger.exception(
            "Unhandled request exception",
            request_body=await _serialize_request_body(request),
            **_build_request_log_context(request),
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error."},
        )
