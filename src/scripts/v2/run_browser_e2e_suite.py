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
    load_latest_seed_summary,
    login_candidate,
    print_detail,
    print_step,
    timestamp_tag,
)

DEFAULT_CANDIDATE_FRONTEND_URL = "http://127.0.0.1:3002"
DEFAULT_ADMIN_FRONTEND_URL = "http://127.0.0.1:3001"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V2 real-browser E2E checks with Playwright.")
    parser.add_argument("--web-base-url", default=DEFAULT_WEB_BASE_URL, help="Candidate API base URL.")
    parser.add_argument("--admin-base-url", default=DEFAULT_ADMIN_BASE_URL, help="Admin API base URL.")
    parser.add_argument("--candidate-frontend-url", default=DEFAULT_CANDIDATE_FRONTEND_URL)
    parser.add_argument("--admin-frontend-url", default=DEFAULT_ADMIN_FRONTEND_URL)
    parser.add_argument("--headed", action="store_true", help="Run browser in headed mode.")
    return parser.parse_args()


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def get_candidate_session(web_base_url: str) -> dict[str, Any]:
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
        return {"accessToken": token, "user": user}


async def get_admin_session(admin_base_url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=admin_base_url.rstrip("/"), timeout=30.0) as client:
        payload = ensure_status(
            await client.post(
                "/v1/auth/login",
                json={
                    "username_or_email": DEFAULT_FLOW_ADMIN_USERNAME,
                    "password": DEFAULT_FLOW_ADMIN_PASSWORD,
                },
            ),
            "Admin login failed",
        )
        return {
            "accessToken": payload["access_token"],
            "refreshToken": payload["refresh_token"],
            "accessTokenExpiresIn": payload["access_token_expires_in"],
            "refreshTokenExpiresIn": payload["refresh_token_expires_in"],
            "tokenType": payload["token_type"],
            "issuedAt": 0,
        }


async def wait_for_frontend_idle(page: Any) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=6_000)
    except Exception:
        await page.wait_for_timeout(1_000)


async def assert_page_health(page: Any, *, label: str, allow_horizontal_overflow: bool = True) -> dict[str, Any]:
    await wait_for_frontend_idle(page)
    body_text = await page.locator("body").inner_text(timeout=10_000)
    assert_true(body_text.strip(), f"{label} rendered empty body.")
    forbidden_snippets = [
        "Request failed with status code",
        "Cannot read properties",
        "Unhandled Runtime Error",
        "Network Error",
    ]
    for snippet in forbidden_snippets:
        assert_true(snippet not in body_text, f"{label} rendered frontend/API error snippet: {snippet}")
    overflow = await page.evaluate(
        "() => Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)"
        " - Math.max(document.documentElement.clientWidth, document.body.clientWidth)"
    )
    if not allow_horizontal_overflow:
        assert_true(float(overflow) <= 4, f"{label} has horizontal overflow: {overflow}px")
    return {
        "label": label,
        "url": page.url,
        "title": await page.title(),
        "body_text_length": len(body_text),
        "horizontal_overflow_px": overflow,
    }


async def main_async() -> int:
    args = parse_args()
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    print_step("Browser E2E: auth redirects, routed pages, exports, and layout guards")

    try:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Playwright is required for this suite. Run `uv add --dev playwright` and `uv run playwright install chromium`."
        ) from exc

    seed_summary = load_latest_seed_summary()
    paths = seed_summary.get("manual_review_paths", {})
    timesheet_path = str(paths.get("timesheet_project_page") or "/timesheets")
    candidate_origin = args.candidate_frontend_url.rstrip("/")
    admin_origin = args.admin_frontend_url.rstrip("/")
    candidate_session = await get_candidate_session(args.web_base_url)
    admin_session = await get_admin_session(args.admin_base_url)
    results: list[dict[str, Any]] = []
    downloads: list[dict[str, Any]] = []
    console_errors: list[dict[str, str]] = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not args.headed)

        clean_context = await browser.new_context(viewport={"width": 1440, "height": 960}, accept_downloads=True)
        clean_page = await clean_context.new_page()
        await clean_page.goto(f"{candidate_origin}/my-contracts", wait_until="domcontentloaded", timeout=25_000)
        await wait_for_frontend_idle(clean_page)
        assert_true("/login" in clean_page.url, "Candidate protected page did not redirect to /login without token.")
        results.append({"label": "candidate_auth_redirect", "url": clean_page.url})
        await clean_page.goto(f"{admin_origin}/contracts", wait_until="domcontentloaded", timeout=25_000)
        await wait_for_frontend_idle(clean_page)
        assert_true("/login" in clean_page.url, "Admin protected page did not redirect to /login without token.")
        results.append({"label": "admin_auth_redirect", "url": clean_page.url})
        await clean_context.close()

        candidate_context = await browser.new_context(viewport={"width": 1440, "height": 960}, accept_downloads=True)
        await candidate_context.add_init_script(
            "window.localStorage.setItem('candidate-web-next-auth-session', "
            f"{json.dumps(json.dumps(candidate_session))});"
        )
        candidate_page = await candidate_context.new_page()
        candidate_page.on(
            "console",
            lambda message: console_errors.append({"type": message.type, "text": message.text})
            if message.type == "error"
            else None,
        )
        for path, label, guard_overflow in [
            ("/jobs", "candidate_jobs", True),
            ("/my-jobs", "candidate_my_jobs", True),
            ("/my-contracts", "candidate_my_contracts", True),
            ("/working-hours", "candidate_working_hours", False),
            ("/referral", "candidate_referral", False),
            ("/earnings", "candidate_earnings", False),
            ("/settings", "candidate_settings", False),
        ]:
            await candidate_page.goto(f"{candidate_origin}{path}", wait_until="domcontentloaded", timeout=25_000)
            results.append(
                await assert_page_health(candidate_page, label=label, allow_horizontal_overflow=guard_overflow)
            )
        await candidate_context.close()

        admin_context = await browser.new_context(viewport={"width": 1440, "height": 960}, accept_downloads=True)
        await admin_context.add_init_script(
            f"window.localStorage.setItem('hr-admin-auth-session', {json.dumps(json.dumps(admin_session))});"
        )
        admin_page = await admin_context.new_page()
        admin_page.on(
            "console",
            lambda message: console_errors.append({"type": message.type, "text": message.text})
            if message.type == "error"
            else None,
        )
        for path, label in [
            ("/dashboard", "admin_dashboard"),
            ("/jobs", "admin_jobs"),
            ("/contracts", "admin_contracts"),
            ("/timesheets", "admin_timesheets"),
            (timesheet_path, "admin_project_timesheet"),
            ("/payments/salary", "admin_salary_records"),
            ("/payments/referrals", "admin_referral_records"),
            ("/candidates", "admin_candidate_pool"),
            ("/mail/templates", "admin_mail_templates"),
        ]:
            await admin_page.goto(f"{admin_origin}{path}", wait_until="domcontentloaded", timeout=25_000)
            results.append(await assert_page_health(admin_page, label=label))

        await admin_page.goto(f"{admin_origin}/candidates", wait_until="domcontentloaded", timeout=25_000)
        await wait_for_frontend_idle(admin_page)
        try:
            async with admin_page.expect_download(timeout=8_000) as download_info:
                await admin_page.get_by_text("导出全部", exact=True).click(timeout=8_000)
            download = await download_info.value
            downloads.append({"name": "candidate_pool_export_all", "suggested_filename": download.suggested_filename})
        except PlaywrightTimeoutError as exc:
            raise AssertionError("Candidate pool export button did not trigger a browser download.") from exc

        await admin_page.goto(f"{admin_origin}{timesheet_path}", wait_until="domcontentloaded", timeout=25_000)
        await wait_for_frontend_idle(admin_page)
        try:
            await admin_page.get_by_role("button", name="导出当前结果").click(timeout=8_000)
            async with admin_page.expect_download(timeout=8_000) as download_info:
                await admin_page.get_by_role("button", name="确认导出").click(timeout=8_000)
            download = await download_info.value
            downloads.append({"name": "timesheet_export_current", "suggested_filename": download.suggested_filename})
        except PlaywrightTimeoutError as exc:
            raise AssertionError("Timesheet export button did not trigger a browser download.") from exc

        await admin_context.close()
        await browser.close()

    serious_console_errors = [
        item
        for item in console_errors
        if "favicon" not in item["text"].lower()
        and "ResizeObserver loop" not in item["text"]
        and "Failed to load resource" not in item["text"]
    ]
    assert_true(not serious_console_errors, f"Browser console errors detected: {serious_console_errors[:5]}")

    report = {
        "generated_at": timestamp_tag(),
        "candidate_frontend_url": candidate_origin,
        "admin_frontend_url": admin_origin,
        "pages": results,
        "downloads": downloads,
        "console_errors_ignored_or_empty": console_errors,
    }
    report_path = TMP_DIR / f"browser-e2e-v2-{timestamp_tag()}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print_detail(f"[PASS] browser_e2e: {len(results)} page checks, {len(downloads)} downloads")
    print_detail(f"report={report_path}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
