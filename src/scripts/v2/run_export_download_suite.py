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
    DEFAULT_PORTAL_CANDIDATE_EMAIL,
    DEFAULT_PORTAL_CANDIDATE_PASSWORD,
    DEFAULT_TIMESHEET_CANDIDATE_EMAIL,
    DEFAULT_TIMESHEET_CANDIDATE_PASSWORD,
    DEFAULT_WEB_BASE_URL,
    TMP_DIR,
    bearer_headers,
    ensure_status,
    load_latest_seed_summary,
    login_admin,
    login_candidate,
    print_detail,
    print_step,
    timestamp_tag,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V2 protected download and export-source checks.")
    parser.add_argument("--web-base-url", default=DEFAULT_WEB_BASE_URL, help="Candidate API base URL.")
    parser.add_argument("--admin-base-url", default=DEFAULT_ADMIN_BASE_URL, help="Admin API base URL.")
    return parser.parse_args()


def assert_status(response: httpx.Response, expected: set[int], label: str) -> None:
    if response.status_code not in expected:
        raise AssertionError(f"{label}: expected {sorted(expected)}, got {response.status_code}: {response.text[:500]}")


def asset_candidates_from_contract(contract_item: dict[str, Any]) -> list[dict[str, Any]]:
    data = contract_item.get("contract_record_data") or contract_item
    assets: list[dict[str, Any]] = []
    for key in (
        "contract_attachment",
        "company_sealed_contract_attachment",
        "candidate_signed_contract_attachment",
        "draft_contract_attachment",
    ):
        asset = data.get(key)
        if isinstance(asset, dict) and asset.get("asset_id"):
            assets.append({"kind": key, **asset})
    return assets


def unique_assets(assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[int] = set()
    unique: list[dict[str, Any]] = []
    for asset in assets:
        asset_id = int(asset.get("asset_id") or 0)
        if asset_id <= 0 or asset_id in seen:
            continue
        seen.add(asset_id)
        unique.append(asset)
    return unique


def normalize_candidate_download_path(download_url: str) -> str:
    if download_url.startswith("http://") or download_url.startswith("https://"):
        marker = "/api/v1"
        index = download_url.find(marker)
        if index >= 0:
            return download_url[index + len(marker) :]
    if download_url.startswith("/api/v1"):
        return download_url[len("/api/v1") :]
    return download_url


async def ensure_reviewer() -> None:
    role = await ensure_reviewer_role(role_name=DEFAULT_REVIEWER_ROLE_NAME)
    await ensure_reviewer_account(
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
    print_step("Export/download: protected assets and frontend export source endpoints")
    await ensure_reviewer()
    seed_summary = load_latest_seed_summary()
    timesheet_demo = seed_summary.get("seed_payloads", {}).get("timesheet_demo", {})
    timesheet_company = timesheet_demo.get("company", {})
    timesheet_project = timesheet_demo.get("project", {})
    company_id = int(timesheet_company.get("id") or 0)
    project_id = int(timesheet_project.get("id") or 0)
    checks: list[dict[str, Any]] = []

    async with (
        httpx.AsyncClient(base_url=args.web_base_url.rstrip("/"), timeout=45.0) as web_client,
        httpx.AsyncClient(base_url=args.admin_base_url.rstrip("/"), timeout=45.0) as admin_client,
    ):
        owner_token = await login_candidate(
            web_client,
            email=DEFAULT_PORTAL_CANDIDATE_EMAIL,
            password=DEFAULT_PORTAL_CANDIDATE_PASSWORD,
        )
        other_token = await login_candidate(
            web_client,
            email=DEFAULT_TIMESHEET_CANDIDATE_EMAIL,
            password=DEFAULT_TIMESHEET_CANDIDATE_PASSWORD,
        )
        admin_token = await login_admin(admin_client, username_or_email="flowadmin", password="FlowAdmin123!")
        reviewer_token = await login_admin(
            admin_client,
            username_or_email=DEFAULT_ASSESSMENT_REVIEWER_USERNAME,
            password=DEFAULT_ASSESSMENT_REVIEWER_PASSWORD,
        )

        contracts = ensure_status(
            await web_client.get("/me/contracts", headers=bearer_headers(owner_token), params={"page_size": 50}),
            "Candidate contract list failed",
        )
        candidate_assets = unique_assets(
            [
                asset
                for item in contracts.get("items", [])
                for asset in asset_candidates_from_contract(item)
                if asset.get("download_url")
            ]
        )
        if not candidate_assets:
            raise AssertionError("No candidate contract asset found for protected download checks.")

        for asset in candidate_assets[:3]:
            path = normalize_candidate_download_path(str(asset["download_url"]))
            response = await web_client.get(path, headers=bearer_headers(owner_token))
            assert_status(response, {200}, f"Candidate owner download {path}")
            if not response.content:
                raise AssertionError(f"Candidate owner download returned empty content: {path}")
            checks.append(
                {
                    "name": "candidate_owner_download",
                    "asset_id": asset.get("asset_id"),
                    "kind": asset.get("kind"),
                    "content_disposition": response.headers.get("content-disposition", ""),
                    "bytes": len(response.content),
                }
            )

            forbidden_response = await web_client.get(path, headers=bearer_headers(other_token))
            assert_status(forbidden_response, {404}, f"Other candidate cannot download {path}")
            checks.append(
                {
                    "name": "candidate_cross_user_download_blocked",
                    "asset_id": asset.get("asset_id"),
                    "status_code": forbidden_response.status_code,
                }
            )

        admin_contracts = ensure_status(
            await admin_client.get("/v1/contracts", headers=bearer_headers(admin_token), params={"page_size": 50}),
            "Admin contract list failed",
        )
        admin_assets = unique_assets(
            [
                asset
                for item in admin_contracts.get("items", [])
                for asset in asset_candidates_from_contract(item)
                if asset.get("asset_id")
            ]
        )
        if not admin_assets:
            admin_assets = candidate_assets

        for asset in admin_assets[:3]:
            asset_id = int(asset["asset_id"])
            download_response = await admin_client.get(f"/v1/assets/{asset_id}/download", headers=bearer_headers(admin_token))
            assert_status(download_response, {200}, f"Admin download asset {asset_id}")
            pdf_response = await admin_client.get(f"/v1/assets/{asset_id}/download-pdf", headers=bearer_headers(admin_token))
            assert_status(pdf_response, {200}, f"Admin PDF download asset {asset_id}")
            if pdf_response.headers.get("content-type", "").split(";")[0] != "application/pdf":
                raise AssertionError(f"Admin PDF download did not return PDF content type for asset {asset_id}.")
            checks.append(
                {
                    "name": "admin_download_and_pdf",
                    "asset_id": asset_id,
                    "download_bytes": len(download_response.content),
                    "pdf_bytes": len(pdf_response.content),
                }
            )

            reviewer_response = await admin_client.get(
                f"/v1/assets/{asset_id}/download",
                headers=bearer_headers(reviewer_token),
            )
            assert_status(reviewer_response, {404}, f"Assessment reviewer cannot download contract asset {asset_id}")
            checks.append(
                {
                    "name": "reviewer_contract_asset_download_blocked",
                    "asset_id": asset_id,
                    "status_code": reviewer_response.status_code,
                }
            )

        talents_source = ensure_status(
            await admin_client.get(
                "/v1/talents",
                headers=bearer_headers(admin_token),
                params={"page": 1, "page_size": 100},
            ),
            "Talent export source query failed",
        )
        if int(talents_source.get("total") or 0) <= 0:
            raise AssertionError("Talent export source query returned no rows.")
        checks.append(
            {
                "name": "talent_export_source_query",
                "total": talents_source.get("total"),
                "page_size": talents_source.get("page_size"),
            }
        )

        if company_id and project_id:
            timesheet_source = ensure_status(
                await admin_client.get(
                    f"/v1/timesheets/companies/{company_id}/projects/{project_id}/workspace",
                    headers=bearer_headers(admin_token),
                ),
                "Timesheet export source query failed",
            )
            checks.append(
                {
                    "name": "timesheet_export_source_query",
                    "records": len(timesheet_source.get("records") or []),
                    "company_id": company_id,
                    "project_id": project_id,
                }
            )

    report = {
        "generated_at": timestamp_tag(),
        "note": "Talent and timesheet CSV exports are generated client-side, so this suite verifies their protected source APIs plus asset downloads.",
        "checks": checks,
    }
    report_path = TMP_DIR / f"export-download-v2-{timestamp_tag()}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print_detail(f"[PASS] export_download: {len(checks)} checks")
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
