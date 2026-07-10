import uuid

import structlog
from fastapi import FastAPI, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from ..core.log_redaction import redact_mapping, serialize_request_body_for_log

logger = structlog.get_logger(__name__)


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
                path_params=redact_mapping(request.path_params),
                query_params=redact_mapping(request.query_params),
                request_body=await serialize_request_body_for_log(request),
            )
        response.headers["X-Request-ID"] = request_id
        return response
