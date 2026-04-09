from uuid import uuid4

import pytest
from httpx import AsyncClient


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_web_register_login_me_refresh_and_logout_flow(
    web_client: AsyncClient,
) -> None:
    suffix = uuid4().hex[:8]
    email = f"web.auth.{suffix}@example.com"
    password = "CandidatePass123!"

    register_response = await web_client.post(
        "/api/v1/user/register",
        json={
            "name": "Web Auth Candidate",
            "email": email,
            "password": password,
        },
    )
    assert register_response.status_code == 201, register_response.text
    register_payload = register_response.json()
    print(f"[web-auth] registered user: id={register_payload['id']} email={register_payload['email']}")
    assert register_payload["name"] == "Web Auth Candidate"
    assert register_payload["email"] == email
    assert register_payload["username"]

    duplicate_register_response = await web_client.post(
        "/api/v1/user/register",
        json={
            "name": "Web Auth Candidate",
            "email": email,
            "password": password,
        },
    )
    assert duplicate_register_response.status_code == 422, duplicate_register_response.text
    assert "Email is already registered" in duplicate_register_response.json()["detail"]

    login_response = await web_client.post(
        "/api/v1/login",
        data={"username": email, "password": password},
    )
    assert login_response.status_code == 200, login_response.text
    login_payload = login_response.json()
    print(f"[web-auth] login token type: {login_payload['token_type']}")
    assert login_payload["token_type"] == "bearer"
    assert login_payload["access_token"]
    assert web_client.cookies.get("refresh_token")

    me_response = await web_client.get(
        "/api/v1/user/me",
        headers={"Authorization": f"Bearer {login_payload['access_token']}"},
    )
    assert me_response.status_code == 200, me_response.text
    me_payload = me_response.json()
    assert me_payload["email"] == email
    assert me_payload["name"] == "Web Auth Candidate"

    refresh_response = await web_client.post("/api/v1/refresh")
    assert refresh_response.status_code == 200, refresh_response.text
    refresh_payload = refresh_response.json()
    print(f"[web-auth] refreshed token type: {refresh_payload['token_type']}")
    assert refresh_payload["token_type"] == "bearer"
    assert refresh_payload["access_token"]

    logout_response = await web_client.post("/api/v1/logout")
    assert logout_response.status_code == 200, logout_response.text
    assert logout_response.json()["message"] == "Logged out successfully."

    refresh_after_logout_response = await web_client.post("/api/v1/refresh")
    assert refresh_after_logout_response.status_code == 401, refresh_after_logout_response.text
