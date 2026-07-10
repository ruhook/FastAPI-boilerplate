import json

import pytest
from starlette.datastructures import QueryParams
from starlette.requests import Request

from src.app.core.log_redaction import (
    REDACTED_VALUE,
    redact_mapping,
    redact_sensitive_data,
    serialize_request_body_for_log,
)

pytestmark = pytest.mark.no_database_cleanup


def build_request(body: bytes, content_type: str, *, method: str = "POST") -> Request:
    consumed = False

    async def receive() -> dict[str, object]:
        nonlocal consumed
        if consumed:
            return {"type": "http.request", "body": b"", "more_body": False}
        consumed = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/test",
            "headers": [(b"content-type", content_type.encode())],
        },
        receive,
    )


def test_redacts_nested_secret_aliases_without_mutating_source() -> None:
    source = {
        "password": "candidate-pass",
        "profile": {"refreshToken": "refresh-value"},
        "accounts": [{"auth_secret": "smtp-code", "email": "mail@example.com"}],
    }

    redacted = redact_sensitive_data(source)

    assert redacted == {
        "password": REDACTED_VALUE,
        "profile": {"refreshToken": REDACTED_VALUE},
        "accounts": [{"auth_secret": REDACTED_VALUE, "email": "mail@example.com"}],
    }
    assert source["password"] == "candidate-pass"


@pytest.mark.parametrize(
    "key",
    [
        "current-password",
        "confirm_password",
        "Access.Token",
        "AUTH_SECRET",
        "verificationCode",
        "Authorization",
        "api-key",
    ],
)
def test_redacts_case_and_format_variants(key: str) -> None:
    assert redact_mapping({key: "visible"}) == {key: REDACTED_VALUE}


def test_preserves_non_sensitive_mapping_and_list_values() -> None:
    source = {"status": "active", "filters": ["engineering", {"email": "a@example.com"}]}

    assert redact_sensitive_data(source) == source


def test_query_parameter_mapping_is_redacted() -> None:
    query = QueryParams("email=alice%40example.com&refresh_token=visible")

    assert redact_mapping(query) == {
        "email": "alice@example.com",
        "refresh_token": REDACTED_VALUE,
    }


@pytest.mark.asyncio
async def test_json_body_is_parsed_and_redacted_before_serialization() -> None:
    request = build_request(
        b'{"email":"alice@example.com","password":"visible","nested":{"access_token":"jwt"}}',
        "application/json; charset=utf-8",
    )

    serialized = await serialize_request_body_for_log(request)

    assert serialized is not None
    assert json.loads(serialized) == {
        "email": "alice@example.com",
        "password": REDACTED_VALUE,
        "nested": {"access_token": REDACTED_VALUE},
    }
    assert "visible" not in serialized
    assert "jwt" not in serialized


@pytest.mark.asyncio
async def test_malformed_json_is_not_logged_raw() -> None:
    request = build_request(b'{"password":"visible"', "application/json")

    assert await serialize_request_body_for_log(request) == "<malformed json body omitted>"


@pytest.mark.asyncio
async def test_urlencoded_password_and_repeated_tokens_are_redacted() -> None:
    request = build_request(
        b"username=alice&password=visible&access_token=first&access_token=second",
        "application/x-www-form-urlencoded",
    )

    assert await serialize_request_body_for_log(request) == (
        "username=alice&password=%5BREDACTED%5D&access_token=%5BREDACTED%5D&access_token=%5BREDACTED%5D"
    )


@pytest.mark.asyncio
async def test_get_and_empty_bodies_are_not_logged() -> None:
    assert await serialize_request_body_for_log(build_request(b"password=visible", "text/plain", method="GET")) is None
    assert await serialize_request_body_for_log(build_request(b"", "application/json")) is None


@pytest.mark.asyncio
async def test_multipart_and_unsupported_bodies_are_omitted() -> None:
    multipart = build_request(b"visible", "multipart/form-data; boundary=example")
    text = build_request(b"password=visible", "text/plain")

    assert await serialize_request_body_for_log(multipart) == "<multipart form-data omitted>"
    assert await serialize_request_body_for_log(text) == "<text/plain body omitted>"


@pytest.mark.asyncio
async def test_serialized_output_is_bounded_after_redaction() -> None:
    request = build_request(
        json.dumps({"password": "visible", "note": "x" * 100}).encode(),
        "application/json",
    )

    serialized = await serialize_request_body_for_log(request, max_length=40)

    assert serialized is not None
    assert serialized.endswith("...(truncated)")
    assert len(serialized) == 40 + len("...(truncated)")
    assert "visible" not in serialized
