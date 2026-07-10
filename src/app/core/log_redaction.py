import json
import re
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import parse_qsl, urlencode

from starlette.requests import Request

REDACTED_VALUE = "[REDACTED]"

_SENSITIVE_EXACT_KEYS = {
    "apikey",
    "authorization",
    "cookie",
    "secretkey",
    "sessionid",
    "setcookie",
    "xapikey",
}
_SENSITIVE_KEY_SUFFIXES = ("password", "token", "secret", "verificationcode")
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]")


def _normalize_key(key: object) -> str:
    return _NON_ALPHANUMERIC.sub("", str(key).lower())


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalize_key(key)
    return normalized in _SENSITIVE_EXACT_KEYS or normalized.endswith(_SENSITIVE_KEY_SUFFIXES)


def redact_sensitive_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: REDACTED_VALUE if _is_sensitive_key(key) else redact_sensitive_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    return value


def redact_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], redact_sensitive_data(mapping))


def _truncate(serialized: str, max_length: int) -> str:
    if len(serialized) <= max_length:
        return serialized
    return f"{serialized[:max_length]}...(truncated)"


async def serialize_request_body_for_log(request: Request, max_length: int = 4000) -> str | None:
    if request.method.upper() in {"GET", "HEAD", "OPTIONS"}:
        return None

    try:
        raw_body = await request.body()
    except Exception:  # pragma: no cover - defensive logging helper
        return "<failed to read request body>"

    if not raw_body:
        return None

    content_type = (request.headers.get("content-type") or "").lower()
    media_type = content_type.partition(";")[0].strip()

    if media_type == "application/json" or media_type.endswith("+json"):
        try:
            parsed = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return "<malformed json body omitted>"
        serialized = json.dumps(redact_sensitive_data(parsed), ensure_ascii=False)
    elif media_type == "application/x-www-form-urlencoded":
        try:
            pairs = parse_qsl(
                raw_body.decode("utf-8"),
                keep_blank_values=True,
                strict_parsing=False,
            )
        except UnicodeDecodeError:
            return "<malformed form body omitted>"
        serialized = urlencode([(key, REDACTED_VALUE if _is_sensitive_key(key) else value) for key, value in pairs])
    elif media_type == "multipart/form-data":
        return "<multipart form-data omitted>"
    else:
        return f"<{media_type or 'binary'} body omitted>"

    return _truncate(serialized, max_length)
