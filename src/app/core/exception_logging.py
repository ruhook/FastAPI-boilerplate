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

from .log_redaction import redact_mapping, serialize_request_body_for_log

logger = structlog.get_logger(__name__)


def _build_request_log_context(request: Request) -> dict[str, Any]:
    return {
        "path_params": redact_mapping(request.path_params),
        "query_params": redact_mapping(request.query_params),
    }


def _validation_errors_without_input(exc: RequestValidationError) -> list[dict[str, Any]]:
    return [{key: value for key, value in error.items() if key != "input"} for error in exc.errors()]


def register_exception_logging(application: FastAPI) -> None:
    @application.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ):
        request.state.error_logged = True
        logger.warning(
            "Request validation failed",
            request_body=await serialize_request_body_for_log(request),
            validation_errors=_validation_errors_without_input(exc),
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
            request_body=await serialize_request_body_for_log(request),
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
            request_body=await serialize_request_body_for_log(request),
            **_build_request_log_context(request),
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error."},
        )
