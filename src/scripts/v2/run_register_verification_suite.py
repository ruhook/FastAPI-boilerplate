from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any

import httpx
from redis.asyncio import Redis

from ...app.core.config import settings
from ...app.modules.user.register_verification_service import _hash_code, _verification_cache_key
from .shared import DEFAULT_WEB_BASE_URL, TMP_DIR, ensure_status, print_detail, print_step, timestamp_tag


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V2 candidate registration verification checks.")
    parser.add_argument("--web-base-url", default=DEFAULT_WEB_BASE_URL, help="Candidate API base URL.")
    parser.add_argument(
        "--include-real-send",
        action="store_true",
        help="Also call /user/register/send-code. This may send a real SMTP email and is disabled by default.",
    )
    parser.add_argument(
        "--real-send-email",
        default="",
        help="Email address used when --include-real-send is enabled.",
    )
    return parser.parse_args()


def assert_status(response: httpx.Response, expected: set[int], label: str) -> None:
    if response.status_code not in expected:
        raise AssertionError(f"{label}: expected {sorted(expected)}, got {response.status_code}: {response.text[:500]}")


async def seed_verification_code(redis: Redis, *, email: str, code: str) -> str:
    cache_key = _verification_cache_key(email)
    payload = {
        "email": email.strip().lower(),
        "code_hash": _hash_code(email, code),
        "sent_at": int(time.time()) - int(settings.CANDIDATE_REGISTER_VERIFICATION_RESEND_COOLDOWN_SECONDS) - 1,
        "attempt_count": 0,
        "seeded_by": "src.scripts.v2.run_register_verification_suite",
    }
    await redis.set(cache_key, json.dumps(payload), ex=int(settings.CANDIDATE_REGISTER_VERIFICATION_CODE_TTL_SECONDS))
    return cache_key


def build_register_payload(*, email: str, code: str) -> dict[str, Any]:
    return {
        "name": "V2 Register Verification",
        "email": email,
        "password": "Candidate123!",
        "verification_code": code,
        "location": "Singapore",
        "native_language": "English",
        "headline": "V2 verification regression candidate",
    }


async def main_async() -> int:
    args = parse_args()
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    unique_tag = timestamp_tag()
    email = f"register.v2.{unique_tag}@example.com"
    valid_code = "246810"
    wrong_code = "135791"
    checks: list[dict[str, Any]] = []

    print_step("Register verification: Redis-backed code validation and registration API")
    redis = Redis.from_url(settings.REDIS_CACHE_URL, decode_responses=True)
    try:
        async with httpx.AsyncClient(base_url=args.web_base_url.rstrip("/"), timeout=30.0) as client:
            no_code_payload = build_register_payload(email=email, code="")
            response = await client.post("/user/register", json=no_code_payload)
            assert_status(response, {422}, "Register without verification code should fail")
            checks.append({"name": "register_without_code", "status_code": response.status_code})

            if args.include_real_send:
                target_email = args.real_send_email.strip() or email
                send_response = await client.post("/user/register/send-code", json={"email": target_email})
                assert_status(send_response, {200, 429}, "Send register verification code")
                checks.append(
                    {
                        "name": "send_code_real_smtp",
                        "email": target_email,
                        "status_code": send_response.status_code,
                    }
                )

            wrong_key = await seed_verification_code(redis, email=email, code=valid_code)
            wrong_response = await client.post("/user/register", json=build_register_payload(email=email, code=wrong_code))
            assert_status(wrong_response, {422}, "Register with wrong verification code should fail")
            checks.append({"name": "register_wrong_code", "status_code": wrong_response.status_code})

            cache_after_wrong = await redis.get(wrong_key)
            if not cache_after_wrong or '"attempt_count": 1' not in cache_after_wrong:
                raise AssertionError("Wrong-code attempt did not update verification attempt_count in Redis.")
            checks.append({"name": "wrong_code_attempt_count_incremented", "cache_key": wrong_key})

            success_key = await seed_verification_code(redis, email=email, code=valid_code)
            success_response = await client.post("/user/register", json=build_register_payload(email=email, code=valid_code))
            assert_status(success_response, {201}, "Register with valid verification code should succeed")
            created_user = success_response.json()
            checks.append({"name": "register_valid_code", "status_code": success_response.status_code, "user_id": created_user.get("id")})

            if await redis.get(success_key):
                raise AssertionError("Verification code cache was not cleared after successful registration.")
            checks.append({"name": "successful_code_consumed", "cache_key": success_key})

            token_payload = ensure_status(
                await client.post(
                    "/login",
                    data={"username": email, "password": "Candidate123!"},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                ),
                "Candidate login after verified registration failed",
            )
            checks.append({"name": "login_after_register", "token_type": token_payload.get("token_type")})

            await seed_verification_code(redis, email=email, code=valid_code)
            duplicate_response = await client.post("/user/register", json=build_register_payload(email=email, code=valid_code))
            assert_status(duplicate_response, {409, 422}, "Duplicate verified registration should fail")
            if "already registered" not in duplicate_response.text:
                raise AssertionError(f"Duplicate register did not return an email-exists error: {duplicate_response.text}")
            checks.append({"name": "duplicate_verified_register_blocked", "status_code": duplicate_response.status_code})
    finally:
        await redis.aclose()

    report = {
        "generated_at": unique_tag,
        "email": email,
        "verification_enabled": bool(settings.CANDIDATE_REGISTER_VERIFICATION_ENABLED),
        "real_send_enabled": bool(args.include_real_send),
        "checks": checks,
    }
    report_path = TMP_DIR / f"register-verification-v2-{timestamp_tag()}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print_detail(f"[PASS] register_verification: {len(checks)} checks")
    print_detail(f"report={report_path}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
