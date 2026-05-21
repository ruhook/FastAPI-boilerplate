from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import httpx

from ..create_assessment_reviewer import (
    DEFAULT_EMAIL as DEFAULT_REVIEWER_EMAIL,
    DEFAULT_NAME as DEFAULT_REVIEWER_NAME,
    DEFAULT_ROLE_NAME as DEFAULT_REVIEWER_ROLE_NAME,
    ensure_reviewer_account,
    ensure_reviewer_role,
)
from ...app.core.db.database import async_engine
from .shared import (
    DEFAULT_ADMIN_BASE_URL,
    DEFAULT_ASSESSMENT_REVIEWER_PASSWORD,
    DEFAULT_ASSESSMENT_REVIEWER_USERNAME,
    DEFAULT_SUPER_ADMIN_PASSWORD,
    DEFAULT_SUPER_ADMIN_USERNAME,
    DEFAULT_TIMESHEET_CANDIDATE_EMAIL,
    DEFAULT_TIMESHEET_CANDIDATE_PASSWORD,
    DEFAULT_WEB_BASE_URL,
    TMP_DIR,
    bearer_headers,
    ensure_status,
    login_admin,
    login_candidate,
    print_detail,
    print_step,
    timestamp_tag,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V2 admin permission matrix regression checks.")
    parser.add_argument("--web-base-url", default=DEFAULT_WEB_BASE_URL, help="Candidate API base URL.")
    parser.add_argument("--admin-base-url", default=DEFAULT_ADMIN_BASE_URL, help="Admin API base URL.")
    return parser.parse_args()


def assert_status(response: httpx.Response, expected: set[int], label: str) -> None:
    if response.status_code not in expected:
        raise AssertionError(f"{label}: expected {sorted(expected)}, got {response.status_code}: {response.text[:500]}")


async def ensure_reviewer() -> dict[str, Any]:
    role = await ensure_reviewer_role(role_name=DEFAULT_REVIEWER_ROLE_NAME)
    return await ensure_reviewer_account(
        role_id=int(role.id),
        name=DEFAULT_REVIEWER_NAME,
        email=DEFAULT_REVIEWER_EMAIL,
        username=DEFAULT_ASSESSMENT_REVIEWER_USERNAME,
        password=DEFAULT_ASSESSMENT_REVIEWER_PASSWORD,
        reset_password=True,
    )


async def main_async() -> int:
    args = parse_args()
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    print_step("Permission matrix: super admin / business admin / assessment reviewer / candidate")
    reviewer_account = await ensure_reviewer()

    async with (
        httpx.AsyncClient(base_url=args.admin_base_url.rstrip("/"), timeout=30.0) as admin_client,
        httpx.AsyncClient(base_url=args.web_base_url.rstrip("/"), timeout=30.0) as web_client,
    ):
        super_token = await login_admin(
            admin_client,
            username_or_email=DEFAULT_SUPER_ADMIN_USERNAME,
            password=DEFAULT_SUPER_ADMIN_PASSWORD,
        )
        business_token = await login_admin(
            admin_client,
            username_or_email="flowadmin",
            password="FlowAdmin123!",
        )
        reviewer_token = await login_admin(
            admin_client,
            username_or_email=DEFAULT_ASSESSMENT_REVIEWER_USERNAME,
            password=DEFAULT_ASSESSMENT_REVIEWER_PASSWORD,
        )
        candidate_token = await login_candidate(
            web_client,
            email=DEFAULT_TIMESHEET_CANDIDATE_EMAIL,
            password=DEFAULT_TIMESHEET_CANDIDATE_PASSWORD,
        )

        cases: list[dict[str, Any]] = []

        async def check(
            *,
            actor: str,
            token: str | None,
            method: str,
            path: str,
            expected: set[int],
            json_payload: dict[str, Any] | None = None,
        ) -> None:
            headers = bearer_headers(token) if token else {}
            response = await admin_client.request(method, path, headers=headers, json=json_payload)
            assert_status(response, expected, f"{actor} {method} {path}")
            cases.append(
                {
                    "actor": actor,
                    "method": method,
                    "path": path,
                    "status_code": response.status_code,
                    "expected": sorted(expected),
                }
            )

        business_read_paths = [
            "/v1/dashboard/metrics",
            "/v1/jobs",
            "/v1/contracts",
            "/v1/talents",
            "/v1/timesheets/overview",
            "/v1/payment-records",
            "/v1/referrals",
            "/v1/mail/templates?include_public=true",
            "/v1/settings/companies",
            "/v1/settings/accounts/reviewers",
            "/v1/settings/dictionaries",
            "/v1/settings/form-templates",
        ]
        settings_super_only_paths = [
            "/v1/settings/accounts",
            "/v1/settings/roles",
            "/v1/settings/permissions/catalog",
        ]
        reviewer_allowed_paths = [
            "/v1/dashboard/metrics",
            "/v1/jobs",
            "/v1/settings/accounts/reviewers",
        ]
        reviewer_forbidden_paths = [
            "/v1/contracts",
            "/v1/talents",
            "/v1/timesheets/overview",
            "/v1/payment-records",
            "/v1/referrals",
            "/v1/mail/templates?include_public=true",
            "/v1/settings/companies",
            "/v1/settings/dictionaries",
            "/v1/settings/form-templates",
        ]

        for path in [*business_read_paths, *settings_super_only_paths]:
            await check(actor="super_admin", token=super_token, method="GET", path=path, expected={200})
        for path in business_read_paths:
            await check(actor="business_admin", token=business_token, method="GET", path=path, expected={200})
        for path in settings_super_only_paths:
            await check(actor="business_admin", token=business_token, method="GET", path=path, expected={403})
        for path in reviewer_allowed_paths:
            await check(actor="assessment_reviewer", token=reviewer_token, method="GET", path=path, expected={200})
        reviewer_jobs = ensure_status(
            await admin_client.get("/v1/jobs?page_size=20", headers=bearer_headers(reviewer_token)),
            "Reviewer jobs scope failed",
        )
        reviewer_job_items = reviewer_jobs.get("items") or []
        if not reviewer_job_items:
            raise AssertionError("Assessment reviewer should see jobs that currently have assessment-review records.")
        reviewer_job_id = int(reviewer_job_items[0]["id"])
        reviewer_progress = ensure_status(
            await admin_client.get(
                f"/v1/jobs/{reviewer_job_id}/progress?stage=assessment",
                headers=bearer_headers(reviewer_token),
            ),
            "Reviewer assessment progress scope failed",
        )
        reviewer_progress_items = reviewer_progress.get("items") or []
        if not reviewer_progress_items:
            raise AssertionError("Assessment reviewer should see all assessment-review records for the selected job.")
        if any(item.get("current_stage") != "assessment_review" for item in reviewer_progress_items):
            raise AssertionError("Assessment reviewer must only receive assessment-review stage rows.")
        for path in reviewer_forbidden_paths:
            await check(actor="assessment_reviewer", token=reviewer_token, method="GET", path=path, expected={403})

        await check(actor="anonymous", token=None, method="GET", path="/v1/jobs", expected={401})
        await check(actor="candidate_token", token=candidate_token, method="GET", path="/v1/jobs", expected={401})

        super_me = ensure_status(
            await admin_client.get("/v1/auth/me", headers=bearer_headers(super_token)),
            "Super admin /me failed",
        )
        business_me = ensure_status(
            await admin_client.get("/v1/auth/me", headers=bearer_headers(business_token)),
            "Business admin /me failed",
        )
        reviewer_me = ensure_status(
            await admin_client.get("/v1/auth/me", headers=bearer_headers(reviewer_token)),
            "Reviewer /me failed",
        )

    report = {
        "generated_at": timestamp_tag(),
        "reviewer_account": reviewer_account,
        "actors": {
            "super_admin": {
                "username": super_me.get("username"),
                "is_superuser": super_me.get("is_superuser"),
                "permission_count": len(super_me.get("permissions") or []),
            },
            "business_admin": {
                "username": business_me.get("username"),
                "is_superuser": business_me.get("is_superuser"),
                "permissions": business_me.get("permissions") or [],
            },
            "assessment_reviewer": {
                "username": reviewer_me.get("username"),
                "is_superuser": reviewer_me.get("is_superuser"),
                "permissions": reviewer_me.get("permissions") or [],
            },
        },
        "cases": cases,
    }
    report_path = TMP_DIR / f"permission-matrix-v2-{timestamp_tag()}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print_detail(f"[PASS] permission_matrix: {len(cases)} checks")
    print_detail(f"report={report_path}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    async def runner() -> int:
        try:
            return await main_async()
        finally:
            await async_engine.dispose()

    raise SystemExit(asyncio.run(runner()))


if __name__ == "__main__":
    main()
