from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import httpx

from .shared import (
    DEFAULT_ADMIN_BASE_URL,
    DEFAULT_FLOW_ADMIN_PASSWORD,
    DEFAULT_FLOW_ADMIN_USERNAME,
    DEFAULT_TIMESHEET_CANDIDATE_EMAIL,
    DEFAULT_TIMESHEET_CANDIDATE_PASSWORD,
    DEFAULT_WEB_BASE_URL,
    TMP_DIR,
    ensure_status,
    login_candidate,
    print_detail,
    print_step,
    timestamp_tag,
)

DEFAULT_CANDIDATE_FRONTEND_URL = "http://127.0.0.1:3002"
DEFAULT_ADMIN_FRONTEND_URL = "http://127.0.0.1:3001"


CANDIDATE_PUBLIC_PATHS = ["/", "/jobs", "/login", "/register"]
CANDIDATE_AUTH_PATHS = [
    "/my-jobs",
    "/my-contracts",
    "/working-hours",
    "/referral",
    "/earnings",
    "/settings",
]
ADMIN_AUTH_PATHS = [
    "/dashboard",
    "/jobs",
    "/contracts",
    "/timesheets",
    "/payments/salary",
    "/payments/referrals",
    "/candidates",
    "/mail/templates",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run V2 browser/static smoke checks for the admin and candidate frontends."
    )
    parser.add_argument("--web-base-url", default=DEFAULT_WEB_BASE_URL, help="Candidate API base URL.")
    parser.add_argument("--admin-base-url", default=DEFAULT_ADMIN_BASE_URL, help="Admin API base URL.")
    parser.add_argument("--candidate-frontend-url", default=DEFAULT_CANDIDATE_FRONTEND_URL)
    parser.add_argument("--admin-frontend-url", default=DEFAULT_ADMIN_FRONTEND_URL)
    parser.add_argument(
        "--require-browser",
        action="store_true",
        help="Fail if Playwright is not installed instead of falling back to static HTTP smoke.",
    )
    parser.add_argument("--headed", action="store_true", help="Run Playwright in headed mode when available.")
    return parser.parse_args()


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def fetch_spa_html(client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    response = await client.get(path, follow_redirects=True)
    if response.status_code >= 400:
        raise RuntimeError(f"GET {path} failed: {response.status_code} {response.text[:300]}")
    content_type = response.headers.get("content-type", "")
    text = response.text
    assert_true("text/html" in content_type.lower() or "<html" in text.lower(), f"{path} did not return HTML.")
    assert_true("<script" in text.lower(), f"{path} does not look like a built SPA HTML entry.")
    return {
        "path": path,
        "status_code": response.status_code,
        "content_length": len(text),
        "final_url": str(response.url),
    }


async def get_candidate_session(*, web_base_url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=web_base_url.rstrip("/"), timeout=30.0) as client:
        token = await login_candidate(
            client,
            email=DEFAULT_TIMESHEET_CANDIDATE_EMAIL,
            password=DEFAULT_TIMESHEET_CANDIDATE_PASSWORD,
        )
        user = ensure_status(
            await client.get("/user/me", headers={"Authorization": f"Bearer {token}"}),
            "Candidate /user/me failed",
        )
        return {
            "accessToken": token,
            "user": user,
        }


async def get_admin_session(*, admin_base_url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=admin_base_url.rstrip("/"), timeout=30.0) as client:
        response = await client.post(
            "/v1/auth/login",
            json={
                "username_or_email": DEFAULT_FLOW_ADMIN_USERNAME,
                "password": DEFAULT_FLOW_ADMIN_PASSWORD,
            },
        )
        payload = ensure_status(response, "Admin login failed")
        return {
            "accessToken": payload["access_token"],
            "refreshToken": payload["refresh_token"],
            "accessTokenExpiresIn": payload["access_token_expires_in"],
            "refreshTokenExpiresIn": payload["refresh_token_expires_in"],
            "tokenType": payload["token_type"],
            "issuedAt": 0,
        }


async def run_static_http_smoke(args: argparse.Namespace) -> dict[str, Any]:
    candidate_results: list[dict[str, Any]] = []
    admin_results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(base_url=args.candidate_frontend_url.rstrip("/"), timeout=15.0) as client:
        for path in [*CANDIDATE_PUBLIC_PATHS, *CANDIDATE_AUTH_PATHS]:
            candidate_results.append(await fetch_spa_html(client, path))

    async with httpx.AsyncClient(base_url=args.admin_frontend_url.rstrip("/"), timeout=15.0) as client:
        for path in ["/login", *ADMIN_AUTH_PATHS]:
            admin_results.append(await fetch_spa_html(client, path))

    candidate_session = await get_candidate_session(web_base_url=args.web_base_url)
    admin_session = await get_admin_session(admin_base_url=args.admin_base_url)
    return {
        "mode": "static_http",
        "reason": "Playwright is not installed in the current Python environment.",
        "candidate_frontend_url": args.candidate_frontend_url.rstrip("/"),
        "admin_frontend_url": args.admin_frontend_url.rstrip("/"),
        "candidate_paths": candidate_results,
        "admin_paths": admin_results,
        "candidate_session_user": {
            "id": candidate_session["user"]["id"],
            "email": candidate_session["user"]["email"],
        },
        "admin_session_token_type": admin_session["tokenType"],
    }


async def run_playwright_smoke(args: argparse.Namespace) -> dict[str, Any]:
    try:
        from playwright.async_api import async_playwright
    except ModuleNotFoundError:
        if args.require_browser:
            raise RuntimeError("Playwright is not installed. Install it before running with --require-browser.")
        return await run_static_http_smoke(args)

    candidate_session = await get_candidate_session(web_base_url=args.web_base_url)
    admin_session = await get_admin_session(admin_base_url=args.admin_base_url)
    candidate_origin = args.candidate_frontend_url.rstrip("/")
    admin_origin = args.admin_frontend_url.rstrip("/")

    console_errors: list[dict[str, str]] = []
    page_results: list[dict[str, Any]] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not args.headed)
        context = await browser.new_context(viewport={"width": 1440, "height": 960})
        page = await context.new_page()
        page.on(
            "console",
            lambda message: console_errors.append({"type": message.type, "text": message.text})
            if message.type in {"error", "warning"}
            else None,
        )

        async def visit(path: str, *, origin: str, storage_script: str | None = None) -> None:
            if storage_script:
                await page.goto(origin)
                await page.evaluate(storage_script)
            response = await page.goto(f"{origin}{path}", wait_until="networkidle", timeout=25_000)
            assert_true(response is not None and response.status < 400, f"Browser visit failed for {origin}{path}")
            content = await page.locator("body").inner_text(timeout=10_000)
            assert_true(content.strip(), f"Browser page {origin}{path} rendered empty body text.")
            assert_true(
                "Request failed with status code" not in content,
                f"Browser page {origin}{path} rendered API error text.",
            )
            page_results.append(
                {
                    "url": f"{origin}{path}",
                    "title": await page.title(),
                    "body_text_length": len(content),
                }
            )

        candidate_storage = (
            "window.localStorage.setItem('candidate-web-next-auth-session', "
            f"{json.dumps(json.dumps(candidate_session))});"
        )
        admin_storage = (
            f"window.localStorage.setItem('hr-admin-auth-session', {json.dumps(json.dumps(admin_session))});"
        )

        for path in CANDIDATE_PUBLIC_PATHS:
            await visit(path, origin=candidate_origin)
        for path in CANDIDATE_AUTH_PATHS:
            await visit(path, origin=candidate_origin, storage_script=candidate_storage)
        for path in ADMIN_AUTH_PATHS:
            await visit(path, origin=admin_origin, storage_script=admin_storage)

        await browser.close()

    serious_console_errors = [
        item for item in console_errors if item["type"] == "error" and "favicon" not in item["text"].lower()
    ]
    assert_true(not serious_console_errors, f"Browser console errors detected: {serious_console_errors[:3]}")
    return {
        "mode": "playwright",
        "candidate_frontend_url": candidate_origin,
        "admin_frontend_url": admin_origin,
        "page_results": page_results,
        "console_warnings_or_errors": console_errors,
    }


async def main_async() -> int:
    args = parse_args()
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    print_step("Browser smoke: frontends and authenticated pages")
    report = await run_playwright_smoke(args)
    report_path = TMP_DIR / f"browser-smoke-v2-{timestamp_tag()}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print_detail(f"[PASS] browser_smoke: mode={report['mode']}")
    print_detail(f"report={report_path}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
