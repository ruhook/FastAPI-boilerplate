# app/middleware/request_id.py
import uuid

import structlog
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = structlog.get_logger(__name__)


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

    if "application/json" in content_type or "application/x-www-form-urlencoded" in content_type:
        text = raw_body.decode("utf-8", errors="replace")
        if len(text) > 4000:
          return f"{text[:4000]}...(truncated)"
        return text
    if "multipart/form-data" in content_type:
        return "<multipart form-data omitted>"
    return f"<{content_type or 'body'} omitted>"


class LoggerMiddleware(BaseHTTPMiddleware):
    """Middleware to add request ID to the context variables.

    Parameters
    ----------
    app: FastAPI
        The FastAPI application instance.
    """

    def __init__(self, app: FastAPI) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """
        Add request ID to the context variables.
        """
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            client_host=request.client.host if request.client else None,
            status_code=None,
            path=request.url.path,
            method=request.method,
        )
        response = await call_next(request)
        structlog.contextvars.bind_contextvars(status_code=response.status_code)
        if response.status_code >= 400 and not getattr(request.state, "error_logged", False):
            logger.warning(
                "HTTP request returned non-success status",
                status_code=response.status_code,
                path_params=dict(request.path_params),
                query_params=dict(request.query_params),
                request_body=await _serialize_request_body(request),
            )
        response.headers["X-Request-ID"] = request_id
        return response
