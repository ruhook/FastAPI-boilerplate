from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from .shared import (
    DEFAULT_ADMIN_BASE_URL,
    DEFAULT_FLOW_ADMIN_PASSWORD,
    DEFAULT_FLOW_ADMIN_USERNAME,
    DEFAULT_PORTAL_CANDIDATE_EMAIL,
    DEFAULT_PORTAL_CANDIDATE_PASSWORD,
    DEFAULT_PROGRESS_CANDIDATE_EMAIL,
    DEFAULT_PROGRESS_CANDIDATE_PASSWORD,
    DEFAULT_TIMESHEET_ADMIN_PASSWORD,
    DEFAULT_TIMESHEET_ADMIN_USERNAME,
    DEFAULT_TIMESHEET_CANDIDATE_EMAIL,
    DEFAULT_TIMESHEET_CANDIDATE_PASSWORD,
    DEFAULT_WEB_BASE_URL,
    EXPECTED_REFERRAL_MILESTONES,
    TMP_DIR,
    build_minimal_docx_bytes,
    build_minimal_pdf_bytes,
    build_minimal_png_bytes,
    build_minimal_xlsx_bytes,
    extract_trailing_json,
    login_admin,
    login_candidate,
    preflight_http_endpoint,
    print_detail,
    print_step,
    quantize_decimal,
    run_module,
    timestamp_tag,
)


@dataclass
class SuiteContext:
    web_base_url: str
    admin_base_url: str
    seed_summary: dict[str, Any] | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V2 API-driven regression checks for the HR portals.")
    parser.add_argument("--web-base-url", default=DEFAULT_WEB_BASE_URL, help="Candidate API base URL.")
    parser.add_argument("--admin-base-url", default=DEFAULT_ADMIN_BASE_URL, help="Admin API base URL.")
    parser.add_argument(
        "--skip-seed",
        action="store_true",
        help="Do not run the consolidated manual seed first.",
    )
    parser.add_argument(
        "--seed-summary-path",
        default="",
        help="Optional path to a previously generated manual-review seed summary JSON file.",
    )
    parser.add_argument(
        "--include-advanced-filter-bulk",
        action="store_true",
        help="Also run the heavier advanced-filter bulk regression after the core suite.",
    )
    return parser.parse_args()


def find_latest_seed_summary_path() -> Path | None:
    candidates = sorted(TMP_DIR.glob("manual-review-seed-v2-*.json"))
    if not candidates:
        return None
    return candidates[-1]


def load_seed_summary(args: argparse.Namespace) -> dict[str, Any] | None:
    if args.seed_summary_path:
        return json.loads(Path(args.seed_summary_path).read_text(encoding="utf-8"))
    if args.skip_seed:
        latest_summary_path = find_latest_seed_summary_path()
        if latest_summary_path is None:
            return None
        return json.loads(latest_summary_path.read_text(encoding="utf-8"))
    seed_run = run_module("src.scripts.v2.seed_manual_review_data", log_prefix="v2-regression-seed")
    summary = extract_trailing_json(seed_run.stdout)
    print_detail(f"seed bundle completed: {seed_run.log_path}")
    return summary


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def normalize_milestones(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in items:
        normalized.append(
            {
                "required_hours": str(quantize_decimal(item.get("required_hours"))),
                "reward_amount": str(quantize_decimal(item.get("reward_amount"))),
            }
        )
    return normalized


async def fetch_json(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    expected_status: int = 200,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    response = await client.request(method, path, headers=headers, **kwargs)
    if response.status_code != expected_status:
        raise RuntimeError(f"{method} {path} failed: {response.status_code} {response.text}")
    return response.json()


async def check_candidate_my_jobs_and_contract_views(context: SuiteContext) -> str:
    async with httpx.AsyncClient(base_url=context.web_base_url, timeout=30.0) as client:
        token = await login_candidate(
            client,
            email=DEFAULT_PORTAL_CANDIDATE_EMAIL,
            password=DEFAULT_PORTAL_CANDIDATE_PASSWORD,
        )
        headers = {"Authorization": f"Bearer {token}"}
        applications = await fetch_json(client, "GET", "/me/applications", headers=headers, params={"page_size": 50})
        items = applications.get("items", [])
        stages = {str(item.get("current_stage")) for item in items}
        expected_stages = {
            "pending_screening",
            "assessment_review",
            "screening_passed",
            "contract_pool",
            "active",
            "rejected",
            "replaced",
        }
        assert_true(expected_stages.issubset(stages), f"Missing expected application stages: {sorted(expected_stages - stages)}")

        needs_action = await fetch_json(
            client,
            "GET",
            "/me/applications",
            headers=headers,
            params={"needs_action_only": "true", "page_size": 50},
        )
        needs_action_stages = {str(item.get("current_stage")) for item in needs_action.get("items", [])}
        assert_true(
            needs_action_stages.issubset({"assessment_review", "screening_passed", "contract_pool"}),
            f"Needs-action returned unexpected stages: {sorted(needs_action_stages)}",
        )

        contract_pool_filtered = await fetch_json(
            client,
            "GET",
            "/me/applications",
            headers=headers,
            params={"current_stage": "contract_pool", "page_size": 50},
        )
        assert_true(contract_pool_filtered.get("items"), "Contract-pool filter returned no items.")
        assert_true(
            all(item.get("current_stage") == "contract_pool" for item in contract_pool_filtered["items"]),
            "Contract-pool filter returned other stages.",
        )

        stage_to_application: dict[str, dict[str, Any]] = {}
        for item in items:
            stage = str(item.get("current_stage"))
            stage_to_application.setdefault(stage, item)

        screening_detail = await fetch_json(
            client,
            "GET",
            f"/me/applications/{stage_to_application['screening_passed']['application_id']}",
            headers=headers,
        )
        active_detail = await fetch_json(
            client,
            "GET",
            f"/me/applications/{stage_to_application['active']['application_id']}",
            headers=headers,
        )
        screening_contract = screening_detail.get("contract_record_data") or {}
        active_contract = active_detail.get("contract_record_data") or {}
        assert_true(bool(screening_detail.get("contract_example_html")), "Screening-passed detail is missing contract example HTML.")
        assert_true(bool(screening_contract.get("draft_contract_attachment")), "Screening-passed detail is missing draft contract.")
        assert_true(bool(active_contract.get("company_sealed_contract_attachment")), "Active detail is missing company sealed contract.")
        assert_true(active_contract.get("contract_status") == "Active", "Active detail did not return Active contract status.")
    return f"applications={applications['total']} needs_action={needs_action['total']} stages_ok={len(expected_stages)}"


async def check_public_jobs_search_and_detail(context: SuiteContext) -> str:
    assert_true(context.seed_summary is not None, "Public jobs check requires seed summary.")
    portal_summary = context.seed_summary["seed_payloads"]["portal_demo_summary"]
    fresh_job_id = int(portal_summary["fresh_job_id"])
    fresh_job_title = str(portal_summary["fresh_job_title"])

    async with httpx.AsyncClient(base_url=context.web_base_url, timeout=30.0) as client:
        list_payload = await fetch_json(
            client,
            "GET",
            "/jobs",
            params={
                "keyword": "Fresh Apply Flow",
                "country": "Brazil",
                "work_mode": "Remote",
                "page_size": 20,
            },
        )
        items = list_payload.get("items", [])
        assert_true(bool(items), "Public jobs search returned no items.")
        matched = next((item for item in items if int(item["id"]) == fresh_job_id), None)
        assert_true(matched is not None, f"Public jobs search did not return seeded fresh job {fresh_job_id}.")

        detail_payload = await fetch_json(client, "GET", f"/jobs/{fresh_job_id}")
        assert_true(detail_payload.get("title") == fresh_job_title, "Public job detail title mismatch.")
        assert_true(bool(detail_payload.get("contract_example_html")), "Public job detail is missing contract example HTML.")
        assert_true(bool(detail_payload.get("form_fields")), "Public job detail is missing hydrated form fields.")
    return f"fresh_job_id={fresh_job_id} search_hits={len(items)}"


async def check_candidate_assessment_upload_guardrails(context: SuiteContext) -> str:
    async with httpx.AsyncClient(base_url=context.web_base_url, timeout=30.0) as client:
        token = await login_candidate(
            client,
            email=DEFAULT_PORTAL_CANDIDATE_EMAIL,
            password=DEFAULT_PORTAL_CANDIDATE_PASSWORD,
        )
        headers = {"Authorization": f"Bearer {token}"}
        applications = await fetch_json(client, "GET", "/me/applications", headers=headers, params={"page_size": 50})
        assessment_item = next(item for item in applications.get("items", []) if item.get("current_stage") == "assessment_review")
        job_id = int(assessment_item["job_id"])

        bad_response = await client.post(
            f"/jobs/{job_id}/assessment/upload",
            headers=headers,
            files={"file": ("assessment.pdf", b"bad", "application/pdf")},
        )
        assert_true(bad_response.status_code == 400, f"Assessment upload should reject PDF, got {bad_response.status_code}.")
        assert_true("Excel" in bad_response.text, f"Unexpected assessment PDF rejection: {bad_response.text}")

        good_response = await client.post(
            f"/jobs/{job_id}/assessment/upload",
            headers=headers,
            files={
                "file": (
                    "assessment.xlsx",
                    build_minimal_xlsx_bytes(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert_true(good_response.status_code == 201, f"Assessment upload should accept XLSX, got {good_response.status_code}: {good_response.text}")
        payload = good_response.json()
        assert_true(payload.get("job_id") == job_id, "Assessment upload response returned the wrong job id.")
    return f"job_id={job_id} rejected_pdf=400 accepted_xlsx=201"


async def check_candidate_signed_contract_guardrails(context: SuiteContext) -> str:
    async with httpx.AsyncClient(base_url=context.web_base_url, timeout=30.0) as client:
        token = await login_candidate(
            client,
            email=DEFAULT_PORTAL_CANDIDATE_EMAIL,
            password=DEFAULT_PORTAL_CANDIDATE_PASSWORD,
        )
        headers = {"Authorization": f"Bearer {token}"}
        contracts = await fetch_json(client, "GET", "/me/contracts", headers=headers, params={"page_size": 20})
        contract_item = next(
            (
                item
                for item in contracts.get("items", [])
                if item.get("current_stage") == "screening_passed"
                or (
                    item.get("current_stage") == "contract_pool"
                    and (item.get("contract_record_data") or {}).get("contract_review") == "待修改"
                )
            ),
            None,
        )
        assert_true(
            contract_item is not None,
            "No candidate contract is currently in an upload-eligible state for the signed-contract regression check.",
        )
        job_id = int(contract_item["job_id"])

        bad_response = await client.post(
            f"/jobs/{job_id}/signed-contract/upload",
            headers=headers,
            files={"file": ("contract.pdf", b"bad", "application/pdf")},
        )
        assert_true(bad_response.status_code == 400, f"Signed contract upload should reject PDF, got {bad_response.status_code}.")
        assert_true(".doc or .docx" in bad_response.text, f"Unexpected signed-contract PDF rejection: {bad_response.text}")

        good_response = await client.post(
            f"/jobs/{job_id}/signed-contract/upload",
            headers=headers,
            files={
                "file": (
                    "contract.docx",
                    build_minimal_docx_bytes(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert_true(
            good_response.status_code == 201,
            f"Signed contract upload should accept DOCX, got {good_response.status_code}: {good_response.text}",
        )
    return f"job_id={job_id} rejected_pdf=400 accepted_docx=201"


async def check_candidate_contract_asset_downloads(context: SuiteContext) -> str:
    async with httpx.AsyncClient(base_url=context.web_base_url, timeout=30.0) as client:
        token = await login_candidate(
            client,
            email=DEFAULT_PORTAL_CANDIDATE_EMAIL,
            password=DEFAULT_PORTAL_CANDIDATE_PASSWORD,
        )
        headers = {"Authorization": f"Bearer {token}"}
        contracts_payload = await fetch_json(client, "GET", "/me/contracts", headers=headers, params={"page_size": 20})
        items = contracts_payload.get("items", [])
        assert_true(bool(items), "Candidate contracts list returned no items.")

        downloaded_assets = 0
        for item in items:
            contract_record = item.get("contract_record_data") or {}
            for asset_key in (
                "contract_attachment",
                "company_sealed_contract_attachment",
                "draft_contract_attachment",
            ):
                asset_payload = contract_record.get(asset_key) or {}
                download_url = asset_payload.get("download_url")
                if not download_url:
                    continue
                normalized_path = str(download_url).removeprefix("/api/v1")
                response = await client.get(normalized_path, headers=headers)
                assert_true(response.status_code == 200, f"Asset download failed for {asset_key}: {response.status_code}")
                assert_true(bool(response.content), f"Asset download for {asset_key} returned empty content.")
                downloaded_assets += 1
                break

        assert_true(downloaded_assets >= 2, f"Expected at least 2 downloadable contract assets, got {downloaded_assets}.")
    return f"contracts={len(items)} downloaded_assets={downloaded_assets}"


async def check_inactive_contract_upload_block(context: SuiteContext) -> str:
    async with httpx.AsyncClient(base_url=context.web_base_url, timeout=30.0) as web_client, httpx.AsyncClient(
        base_url=context.admin_base_url,
        timeout=30.0,
    ) as admin_client:
        candidate_token = await login_candidate(
            web_client,
            email=DEFAULT_PORTAL_CANDIDATE_EMAIL,
            password=DEFAULT_PORTAL_CANDIDATE_PASSWORD,
        )
        admin_token = await login_admin(
            admin_client,
            username_or_email=DEFAULT_FLOW_ADMIN_USERNAME,
            password=DEFAULT_FLOW_ADMIN_PASSWORD,
        )
        candidate_headers = {"Authorization": f"Bearer {candidate_token}"}
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        contracts = await fetch_json(web_client, "GET", "/me/contracts", headers=candidate_headers, params={"page_size": 20})
        target = next(
            item
            for item in contracts.get("items", [])
            if item.get("current_stage") in {"screening_passed", "contract_pool"}
            and (item.get("contract_record_data") or {}).get("draft_contract_attachment")
        )
        contract_record = target.get("contract_record_data") or {}
        contract_record_id = int(contract_record["id"])
        original_status = str(contract_record.get("contract_status") or "Pending Activation")
        job_id = int(target["job_id"])

        try:
            update_response = await admin_client.patch(
                f"/v1/contracts/{contract_record_id}",
                headers=admin_headers,
                json={"contract_status": "Terminated"},
            )
            assert_true(update_response.status_code == 200, f"Failed to terminate contract for guardrail check: {update_response.text}")

            upload_response = await web_client.post(
                f"/jobs/{job_id}/signed-contract/upload",
                headers=candidate_headers,
                files={
                    "file": (
                        "blocked.docx",
                        build_minimal_docx_bytes("inactive contract should be blocked"),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )
            assert_true(
                upload_response.status_code == 400,
                f"Inactive contract upload should be blocked, got {upload_response.status_code}: {upload_response.text}",
            )
            assert_true("inactive" in upload_response.text.lower(), f"Unexpected inactive upload rejection: {upload_response.text}")
        finally:
            restore_response = await admin_client.patch(
                f"/v1/contracts/{contract_record_id}",
                headers=admin_headers,
                json={"contract_status": original_status},
            )
            if restore_response.status_code >= 400:
                raise RuntimeError(
                    f"Failed to restore contract {contract_record_id} to {original_status}: "
                    f"{restore_response.status_code} {restore_response.text}"
                )
    return f"contract_record_id={contract_record_id} temporarily_terminated_and_restored"


async def check_candidate_timesheet_dashboard(context: SuiteContext) -> str:
    async with httpx.AsyncClient(base_url=context.web_base_url, timeout=30.0) as client:
        token = await login_candidate(
            client,
            email=DEFAULT_TIMESHEET_CANDIDATE_EMAIL,
            password=DEFAULT_TIMESHEET_CANDIDATE_PASSWORD,
        )
        headers = {"Authorization": f"Bearer {token}"}
        month = date.today().strftime("%Y-%m")
        payload = await fetch_json(client, "GET", "/me/timesheets", headers=headers, params={"bonus_month": month})
        contracts = payload.get("contracts", [])
        assert_true(bool(contracts), "Timesheet workspace returned no contracts.")
        assert_true(bool(payload.get("team_leader_bonus")), "Team leader bonus payload is missing.")
        statuses = {str(item.get("contract_status")) for item in contracts}
        assert_true("Active" in statuses and "Terminated" in statuses, f"Unexpected timesheet contract statuses: {sorted(statuses)}")

        for contract in contracts:
            dashboard = contract.get("dashboard") or {}
            hours = quantize_decimal(dashboard.get("total_work_hours"))
            rate = quantize_decimal(contract.get("rate"))
            expected_income = quantize_decimal(hours * rate)
            actual_income = quantize_decimal(dashboard.get("estimated_income"))
            assert_true(
                actual_income == expected_income,
                f"Contract {contract.get('contract_record_id')} estimated income mismatch: "
                f"expected {expected_income} got {actual_income}",
            )
    return f"contracts={len(contracts)} statuses={sorted(statuses)} bonus_month={payload.get('bonus_month')}"


async def check_candidate_referrals_and_earnings(context: SuiteContext) -> str:
    async with httpx.AsyncClient(base_url=context.web_base_url, timeout=30.0) as client:
        token = await login_candidate(
            client,
            email=DEFAULT_TIMESHEET_CANDIDATE_EMAIL,
            password=DEFAULT_TIMESHEET_CANDIDATE_PASSWORD,
        )
        headers = {"Authorization": f"Bearer {token}"}
        referral_payload = await fetch_json(client, "GET", "/me/referrals", headers=headers)
        milestones = normalize_milestones(referral_payload.get("milestones", []))
        assert_true(milestones == EXPECTED_REFERRAL_MILESTONES, f"Candidate referral milestones mismatch: {milestones}")
        assert_true(int(referral_payload.get("active_referral_count") or 0) > 0, "Candidate referral dashboard should have active referrals.")

        earnings_response = await client.get("/me/earnings", headers=headers, params={"payment_type": "invalid_type"})
        assert_true(earnings_response.status_code == 400, f"Candidate earnings invalid payment_type should return 400, got {earnings_response.status_code}.")
    return f"milestones_ok={len(milestones)} active_referrals={referral_payload.get('active_referral_count')}"


async def check_admin_referrals_and_payment_filters(context: SuiteContext) -> str:
    async with httpx.AsyncClient(base_url=context.admin_base_url, timeout=30.0) as client:
        flow_admin_token = await login_admin(
            client,
            username_or_email=DEFAULT_FLOW_ADMIN_USERNAME,
            password=DEFAULT_FLOW_ADMIN_PASSWORD,
        )
        headers = {"Authorization": f"Bearer {flow_admin_token}"}

        referrals_payload = await fetch_json(client, "GET", "/v1/referrals", headers=headers, params={"page_size": 5})
        milestones = normalize_milestones(referrals_payload.get("milestones", []))
        assert_true(milestones == EXPECTED_REFERRAL_MILESTONES, f"Admin referral milestones mismatch: {milestones}")
        assert_true(int(referrals_payload.get("total") or 0) > 0, "Admin referrals should contain seeded referral records.")

        payment_response = await client.get("/v1/payment-records", headers=headers, params={"payment_type": "invalid_type"})
        assert_true(payment_response.status_code == 400, f"Admin payment invalid payment_type should return 400, got {payment_response.status_code}.")
    return f"milestones_ok={len(milestones)} referrals_total={referrals_payload.get('total')}"


async def check_admin_contracts_and_talents_queries(context: SuiteContext) -> str:
    assert_true(context.seed_summary is not None, "Admin contracts/talents check requires seed summary.")
    async with httpx.AsyncClient(base_url=context.admin_base_url, timeout=30.0) as client:
        flow_admin_token = await login_admin(
            client,
            username_or_email=DEFAULT_FLOW_ADMIN_USERNAME,
            password=DEFAULT_FLOW_ADMIN_PASSWORD,
        )
        headers = {"Authorization": f"Bearer {flow_admin_token}"}

        timesheet_seed = context.seed_summary["seed_payloads"]["timesheet_demo"]
        company_info = timesheet_seed["company"]
        project_info = timesheet_seed["project"]

        contracts_payload = await fetch_json(
            client,
            "GET",
            "/v1/contracts",
            headers=headers,
            params={
                "company_id": company_info["id"],
                "keyword": "TMX-TS-20",
                "page_size": 50,
            },
        )
        contract_items = contracts_payload.get("items", [])
        assert_true(bool(contract_items), "Admin contracts query returned no items for the seeded company.")
        assert_true(
            any(str(item.get("agreement_ref_no") or "").startswith("TMX-TS-20") for item in contract_items),
            "Admin contracts query did not return the seeded agreement refs.",
        )

        talents_payload = await fetch_json(
            client,
            "GET",
            "/v1/talents",
            headers=headers,
            params={
                "company_id": company_info["id"],
                "project_id": project_info["id"],
                "keyword": "Ana",
                "page_size": 50,
            },
        )
        talent_items = talents_payload.get("items", [])
        assert_true(bool(talent_items), "Admin talents query returned no items for the seeded project.")
        assert_true(
            any("Ana" in str(item.get("full_name") or "") for item in talent_items),
            "Admin talents query did not return the expected seeded worker.",
        )
    return f"contracts={len(contract_items)} talents={len(talent_items)}"


async def check_admin_timesheet_and_progress_endpoints(context: SuiteContext) -> str:
    async with httpx.AsyncClient(base_url=context.admin_base_url, timeout=30.0) as client:
        token = await login_admin(
            client,
            username_or_email=DEFAULT_TIMESHEET_ADMIN_USERNAME,
            password=DEFAULT_TIMESHEET_ADMIN_PASSWORD,
        )
        headers = {"Authorization": f"Bearer {token}"}

        overview_payload = await fetch_json(client, "GET", "/v1/timesheets/overview", headers=headers)
        overview_items = overview_payload.get("items", [])
        assert_true(bool(overview_items), "Admin timesheet overview returned no items.")

        if context.seed_summary:
            timesheet_seed = context.seed_summary["seed_payloads"]["timesheet_demo"]
            company_info = timesheet_seed["company"]
            project_info = timesheet_seed["project"]
            workspace_payload = await fetch_json(
                client,
                "GET",
                f"/v1/timesheets/companies/{company_info['id']}/projects/{project_info['id']}/workspace",
                headers=headers,
            )
            assert_true("records" in workspace_payload, "Admin timesheet workspace payload is missing records.")

        flow_admin_token = await login_admin(
            client,
            username_or_email=DEFAULT_FLOW_ADMIN_USERNAME,
            password=DEFAULT_FLOW_ADMIN_PASSWORD,
        )
        flow_headers = {"Authorization": f"Bearer {flow_admin_token}"}
        if context.seed_summary and context.seed_summary["manual_review_paths"]["progress_jobs"]:
            first_progress_path = str(context.seed_summary["manual_review_paths"]["progress_jobs"][0])
            job_id = int(first_progress_path.split("/")[2])
            progress_payload = await fetch_json(client, "GET", f"/v1/jobs/{job_id}/progress", headers=flow_headers)
            assert_true(bool(progress_payload.get("items")), "Admin progress endpoint returned no items.")
            return f"overview_items={len(overview_items)} progress_items={len(progress_payload.get('items', []))}"
    return f"overview_items={len(overview_items)}"


async def check_admin_payment_record_mutations(context: SuiteContext) -> str:
    assert_true(context.seed_summary is not None, "Payment mutation check requires seed summary.")
    worker = context.seed_summary["seed_payloads"]["timesheet_demo"]["workers"][0]
    worker_email = str(worker["email"])

    async with httpx.AsyncClient(base_url=context.admin_base_url, timeout=30.0) as admin_client, httpx.AsyncClient(
        base_url=context.web_base_url,
        timeout=30.0,
    ) as web_client:
        admin_token = await login_admin(
            admin_client,
            username_or_email=DEFAULT_FLOW_ADMIN_USERNAME,
            password=DEFAULT_FLOW_ADMIN_PASSWORD,
        )
        candidate_token = await login_candidate(
            web_client,
            email=worker_email,
            password=DEFAULT_TIMESHEET_CANDIDATE_PASSWORD,
        )
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        candidate_headers = {"Authorization": f"Bearer {candidate_token}"}

        worker_contracts_payload = await fetch_json(
            admin_client,
            "GET",
            "/v1/contracts",
            headers=admin_headers,
            params={"keyword": worker_email, "page_size": 20},
        )
        worker_contract = next(
            item
            for item in worker_contracts_payload.get("items", [])
            if item.get("contract_status") == "Active"
        )
        worker_user_id = int(worker_contract["user_id"])
        marker = f"V2PAY-{timestamp_tag()}"
        create_payload = {
            "items": [
                {
                    "user_id": worker_user_id,
                    "payment_type": "salary",
                    "amount": "128.55",
                    "currency": "USD",
                    "contract_record_id": int(worker_contract["id"]),
                    "external_platform": "manual_test",
                    "external_transaction_no": f"{marker}-SALARY",
                    "remark": "V2 salary regression payout.",
                },
                {
                    "user_id": worker_user_id,
                    "payment_type": "team_leader_bonus",
                    "amount": "9.90",
                    "currency": "USD",
                    "contract_record_id": int(worker_contract["id"]),
                    "external_platform": "manual_test",
                    "external_transaction_no": f"{marker}-TL",
                    "remark": "V2 team leader regression payout.",
                },
            ]
        }
        create_result = await fetch_json(
            admin_client,
            "POST",
            "/v1/payment-records/batch",
            headers=admin_headers,
            json=create_payload,
        )
        created_items = create_result.get("items", [])
        assert_true(int(create_result.get("created_count") or 0) == 2, "Expected two payment records to be created.")
        assert_true(len(created_items) == 2, "Payment batch response returned an unexpected item count.")

        invalid_referral_payload = {
            "items": [
                {
                    "user_id": worker_user_id,
                    "payment_type": "referral_reward",
                    "amount": "12.00",
                    "currency": "USD",
                    "referral_record_id": 1,
                    "external_transaction_no": f"{marker}-REFERRAL",
                }
            ]
        }
        invalid_response = await admin_client.post(
            "/v1/payment-records/batch",
            headers=admin_headers,
            json=invalid_referral_payload,
        )
        assert_true(
            invalid_response.status_code == 400,
            f"Manual referral reward creation should be rejected, got {invalid_response.status_code}: {invalid_response.text}",
        )
        assert_true(
            "referral rewards page" in invalid_response.text,
            f"Unexpected manual referral payment rejection: {invalid_response.text}",
        )

        payment_list_payload = await fetch_json(
            admin_client,
            "GET",
            "/v1/payment-records",
            headers=admin_headers,
            params={"keyword": marker, "page_size": 20},
        )
        matching_payment_records = payment_list_payload.get("items", [])
        assert_true(
            len(matching_payment_records) >= 2,
            f"Expected at least two payment records for marker {marker}, got {len(matching_payment_records)}.",
        )

        earnings_payload = await fetch_json(
            web_client,
            "GET",
            "/me/earnings",
            headers=candidate_headers,
            params={"month": date.today().strftime("%Y-%m"), "page_size": 100},
        )
        earnings_matches = [
            item
            for item in earnings_payload.get("items", [])
            if marker in str(item.get("external_transaction_no") or "")
        ]
        assert_true(
            len(earnings_matches) >= 2,
            f"Candidate earnings did not surface both new manual payments for marker {marker}.",
        )
    return f"payment_marker={marker} user={worker_email} created=2 earnings_matches={len(earnings_matches)}"


async def check_admin_referral_mark_paid_mutation(context: SuiteContext) -> str:
    assert_true(context.seed_summary is not None, "Referral mark-paid mutation check requires seed summary.")
    timesheet_seed = context.seed_summary["seed_payloads"]["timesheet_demo"]
    company_id = int(timesheet_seed["company"]["id"])
    project_id = int(timesheet_seed["project"]["id"])

    async with httpx.AsyncClient(base_url=context.admin_base_url, timeout=30.0) as admin_client, httpx.AsyncClient(
        base_url=context.web_base_url,
        timeout=30.0,
    ) as web_client:
        admin_token = await login_admin(
            admin_client,
            username_or_email=DEFAULT_FLOW_ADMIN_USERNAME,
            password=DEFAULT_FLOW_ADMIN_PASSWORD,
        )
        candidate_token = await login_candidate(
            web_client,
            email=DEFAULT_TIMESHEET_CANDIDATE_EMAIL,
            password=DEFAULT_TIMESHEET_CANDIDATE_PASSWORD,
        )
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        candidate_headers = {"Authorization": f"Bearer {candidate_token}"}

        referrals_payload = await fetch_json(
            admin_client,
            "GET",
            "/v1/referrals",
            headers=admin_headers,
            params={"page_size": 20},
        )
        target_group = next(
            group for group in referrals_payload.get("items", []) if group.get("referrer_email") == DEFAULT_TIMESHEET_CANDIDATE_EMAIL
        )
        target_record = next(
            (
                child
                for child in target_group.get("children", [])
                if quantize_decimal(child.get("payable_reward_amount")) > 0
            ),
            None,
        )

        workspace_payload = await fetch_json(
            admin_client,
            "GET",
            f"/v1/timesheets/companies/{company_id}/projects/{project_id}/workspace",
            headers=admin_headers,
        )
        available_workers = [item for item in workspace_payload.get("available_workers", []) if item.get("contract_record_id")]
        leader_user_id = int(
            (workspace_payload.get("records", [{}])[0] or {}).get("team_leader_user_id")
            or available_workers[0]["user_id"]
        )
        if target_record is None:
            target_record = next(
                (
                    child
                    for child in target_group.get("children", [])
                    if quantize_decimal(child.get("paid_reward_amount")) < Decimal("300.00")
                ),
                None,
            )
            assert_true(
                target_record is not None,
                "All referral demo candidates have already been fully paid to the USD 300 cap.",
            )
            referred_worker = next(
                item for item in available_workers if item.get("email") == target_record.get("referred_email")
            )
            current_work_hours = quantize_decimal(target_record.get("work_hours"))
            additional_hours = max(Decimal("0.10"), Decimal("310.00") - current_work_hours)
            top_up_marker = f"V2-REFERRAL-HOURS-{timestamp_tag()}"
            top_up_create_payload = await fetch_json(
                admin_client,
                "POST",
                f"/v1/timesheets/companies/{company_id}/projects/{project_id}/records/batch",
                headers=admin_headers,
                expected_status=201,
                json={
                    "sub_project_name": top_up_marker,
                    "work_date": date.today().isoformat(),
                    "language": str((workspace_payload.get("timesheet_languages") or ["en-US"])[0]),
                    "project_link": f"https://example.com/{top_up_marker.lower()}",
                    "human_efficiency_minutes": 5,
                    "team_leader_user_id": leader_user_id,
                    "entries": [
                        {
                            "contract_record_id": int(referred_worker["contract_record_id"]),
                            "user_id": int(referred_worker["user_id"]),
                            "work_type": str((workspace_payload.get("timesheet_work_types") or ["Annotation"])[0]),
                            "output_quantity": 40,
                            "customer_duration_hours": f"{additional_hours:.2f}",
                            "candidate_duration_hours": f"{additional_hours:.2f}",
                            "role_name": str((workspace_payload.get("timesheet_roles") or ["Annotator"])[0]),
                            "non_operational_duration_hours": "0.00",
                            "note_asset_ids": [],
                            "extra_notes": top_up_marker,
                            "poc_evaluation": top_up_marker,
                        }
                    ],
                },
            )
            assert_true(
                int(top_up_create_payload.get("created_count") or 0) == 1,
                "Referral top-up timesheet record was not created successfully.",
            )

            referrals_payload = await fetch_json(
                admin_client,
                "GET",
                "/v1/referrals",
                headers=admin_headers,
                params={"page_size": 20},
            )
            target_group = next(
                group for group in referrals_payload.get("items", []) if group.get("referrer_email") == DEFAULT_TIMESHEET_CANDIDATE_EMAIL
            )
            target_record = next(
                (
                    child
                    for child in target_group.get("children", [])
                    if child.get("referred_email") == referred_worker["email"]
                    and quantize_decimal(child.get("payable_reward_amount")) > 0
                ),
                None,
            )
            assert_true(
                target_record is not None,
                "Referral top-up did not produce a payable referral reward.",
            )

        referral_record_id = int(target_record["id"])
        referred_email = str(target_record.get("referred_email") or "")

        mark_paid_payload = await fetch_json(
            admin_client,
            "POST",
            f"/v1/referrals/{referral_record_id}/mark-paid",
            headers=admin_headers,
        )
        marked_item = mark_paid_payload.get("item") or {}
        assert_true(str(marked_item.get("payout_status")) == "paid", "Marked referral did not move to paid status.")
        assert_true(
            quantize_decimal(marked_item.get("payable_reward_amount")) == Decimal("0.00"),
            "Marked referral still has a payable balance.",
        )
        assert_true(
            quantize_decimal(marked_item.get("paid_reward_amount"))
            == quantize_decimal(marked_item.get("referral_earnings")),
            "Paid referral did not close the paid/reward amounts.",
        )

        duplicate_response = await admin_client.post(
            f"/v1/referrals/{referral_record_id}/mark-paid",
            headers=admin_headers,
        )
        assert_true(
            duplicate_response.status_code == 400,
            f"Duplicate referral mark-paid should fail with 400, got {duplicate_response.status_code}: {duplicate_response.text}",
        )
        assert_true(
            "no unpaid referral reward" in duplicate_response.text.lower(),
            f"Unexpected duplicate mark-paid rejection: {duplicate_response.text}",
        )

        payment_records_payload = await fetch_json(
            admin_client,
            "GET",
            "/v1/payment-records",
            headers=admin_headers,
            params={"payment_type": "referral_reward", "keyword": referred_email, "page_size": 20},
        )
        payment_match = next(
            (
                item
                for item in payment_records_payload.get("items", [])
                if int(item.get("referral_record_id") or 0) == referral_record_id
            ),
            None,
        )
        assert_true(payment_match is not None, "Referral mark-paid did not create a matching payment record.")

        candidate_referrals_payload = await fetch_json(
            web_client,
            "GET",
            "/me/referrals",
            headers=candidate_headers,
        )
        candidate_record = next(
            (
                item
                for item in candidate_referrals_payload.get("items", [])
                if int(item.get("id") or 0) == referral_record_id
            ),
            None,
        )
        assert_true(candidate_record is not None, "Candidate referral dashboard did not return the marked referral record.")
        assert_true(
            str(candidate_record.get("payout_status")) == "paid",
            "Candidate referral dashboard did not reflect paid status.",
        )
    return f"referral_record_id={referral_record_id} paid_amount={marked_item.get('paid_reward_amount')}"


async def check_admin_contract_mutations(context: SuiteContext) -> str:
    async with httpx.AsyncClient(base_url=context.admin_base_url, timeout=30.0) as client:
        token = await login_admin(
            client,
            username_or_email=DEFAULT_FLOW_ADMIN_USERNAME,
            password=DEFAULT_FLOW_ADMIN_PASSWORD,
        )
        headers = {"Authorization": f"Bearer {token}"}

        portal_contracts_payload = await fetch_json(
            client,
            "GET",
            "/v1/contracts",
            headers=headers,
            params={"keyword": DEFAULT_PORTAL_CANDIDATE_EMAIL, "page_size": 100},
        )
        portal_contracts = portal_contracts_payload.get("items", [])
        pending_contract = next(item for item in portal_contracts if item.get("contract_status") == "Pending Activation")
        active_contract = next(
            item for item in portal_contracts if item.get("contract_status") == "Active" and bool(item.get("is_current"))
        )

        invalid_activate_response = await client.patch(
            f"/v1/contracts/{pending_contract['id']}",
            headers=headers,
            json={"contract_status": "Active"},
        )
        assert_true(
            invalid_activate_response.status_code == 400,
            f"Pending contract activation bypass should fail, got {invalid_activate_response.status_code}: {invalid_activate_response.text}",
        )
        assert_true(
            "company signed contract workflow" in invalid_activate_response.text,
            f"Unexpected pending contract activation rejection: {invalid_activate_response.text}",
        )

        timesheet_contracts_payload = await fetch_json(
            client,
            "GET",
            "/v1/contracts",
            headers=headers,
            params={"keyword": DEFAULT_TIMESHEET_CANDIDATE_EMAIL, "page_size": 100},
        )
        timesheet_contracts = timesheet_contracts_payload.get("items", [])
        terminated_contract = next(item for item in timesheet_contracts if item.get("contract_status") == "Terminated")
        patch_marker = f"V2-LEGACY-{timestamp_tag()}"
        patched_payload = await fetch_json(
            client,
            "PATCH",
            f"/v1/contracts/{terminated_contract['id']}",
            headers=headers,
            json={
                "agreement_ref_no": patch_marker,
                "contract_type": "normal",
                "end_date": date.today().isoformat(),
            },
        )
        patched_item = patched_payload.get("item") or {}
        assert_true(str(patched_item.get("agreement_ref_no")) == patch_marker, "Contract patch did not persist the ref no.")
        assert_true(str(patched_item.get("end_date")) == date.today().isoformat(), "Contract patch did not persist the end date.")

        resign_marker = f"V2-RESIGN-{timestamp_tag()}"
        resign_response = await client.post(
            f"/v1/contracts/{active_contract['id']}/resign",
            headers=headers,
            data={
                "agreement_ref_no": resign_marker,
                "contract_status": "Active",
                "contract_type": active_contract.get("contract_type") or "normal",
                "contractor_name": active_contract.get("contractor_name") or "Portal Candidate",
                "rate": active_contract.get("rate") or "18.00",
                "legal_entity": active_contract.get("legal_entity") or "T-Maxx International",
                "worker_type": active_contract.get("worker_type") or "Contractor",
                "effective_date": date.today().isoformat(),
            },
            files={
                "file": (
                    "resigned-contract.pdf",
                    build_minimal_pdf_bytes(f"Contract re-signed via {resign_marker}"),
                    "application/pdf",
                )
            },
        )
        assert_true(
            resign_response.status_code == 200,
            f"Contract re-sign should succeed, got {resign_response.status_code}: {resign_response.text}",
        )
        resigned_item = (resign_response.json() or {}).get("item") or {}
        assert_true(bool(resigned_item.get("is_current")), "Re-signed contract should be the current version.")
        assert_true(
            int(resigned_item.get("previous_contract_record_id") or 0) == int(active_contract["id"]),
            "Re-signed contract did not link back to the previous contract version.",
        )
        assert_true(str(resigned_item.get("agreement_ref_no")) == resign_marker, "Re-signed contract ref no mismatch.")

        resigned_lookup_payload = await fetch_json(
            client,
            "GET",
            "/v1/contracts",
            headers=headers,
            params={"keyword": resign_marker, "page_size": 10},
        )
        assert_true(
            any(int(item.get("id") or 0) == int(resigned_item["id"]) for item in resigned_lookup_payload.get("items", [])),
            "Re-signed contract lookup did not return the new version.",
        )
    return f"patched_contract={terminated_contract['id']} resigned_contract={resigned_item.get('id')}"


async def check_admin_timesheet_mutations(context: SuiteContext) -> str:
    assert_true(context.seed_summary is not None, "Timesheet mutation check requires seed summary.")
    timesheet_seed = context.seed_summary["seed_payloads"]["timesheet_demo"]
    company_id = int(timesheet_seed["company"]["id"])
    project_id = int(timesheet_seed["project"]["id"])

    async with httpx.AsyncClient(base_url=context.admin_base_url, timeout=30.0) as client:
        token = await login_admin(
            client,
            username_or_email=DEFAULT_TIMESHEET_ADMIN_USERNAME,
            password=DEFAULT_TIMESHEET_ADMIN_PASSWORD,
        )
        headers = {"Authorization": f"Bearer {token}"}

        workspace_payload = await fetch_json(
            client,
            "GET",
            f"/v1/timesheets/companies/{company_id}/projects/{project_id}/workspace",
            headers=headers,
        )
        available_workers = [item for item in workspace_payload.get("available_workers", []) if item.get("contract_record_id")]
        assert_true(bool(available_workers), "No active workers were returned for the timesheet workspace.")
        leader_user_id = int(
            (workspace_payload.get("records", [{}])[0] or {}).get("team_leader_user_id")
            or available_workers[0]["user_id"]
        )
        language = str((workspace_payload.get("timesheet_languages") or ["en-US"])[0])
        work_type = str((workspace_payload.get("timesheet_work_types") or ["Annotation"])[0])
        role_name = str((workspace_payload.get("timesheet_roles") or ["Annotator"])[0])

        note_upload_response = await client.post(
            "/v1/assets/upload",
            headers=headers,
            data={"type": "timesheet_note", "module": "timesheet"},
            files={"file": ("timesheet-note.png", build_minimal_png_bytes(), "image/png")},
        )
        assert_true(
            note_upload_response.status_code == 201,
            f"Timesheet note asset upload should succeed, got {note_upload_response.status_code}: {note_upload_response.text}",
        )
        note_asset_id = int((note_upload_response.json() or {}).get("id") or 0)
        assert_true(note_asset_id > 0, "Timesheet note upload did not return a valid asset id.")

        invalid_marker = f"V2-TS-INVALID-{timestamp_tag()}"
        invalid_create_response = await client.post(
            f"/v1/timesheets/companies/{company_id}/projects/{project_id}/records/batch",
            headers=headers,
            json={
                "sub_project_name": invalid_marker,
                "work_date": date.today().isoformat(),
                "language": language,
                "project_link": f"https://example.com/{invalid_marker.lower()}",
                "human_efficiency_minutes": 5,
                "team_leader_user_id": leader_user_id,
                "entries": [
                    {
                        "contract_record_id": int(available_workers[0]["contract_record_id"]),
                        "user_id": int(available_workers[0]["user_id"]),
                        "work_type": work_type,
                        "output_quantity": "1.50",
                        "customer_duration_hours": "1.00",
                        "candidate_duration_hours": "1.00",
                        "role_name": role_name,
                        "non_operational_duration_hours": "0.00",
                        "note_asset_ids": [],
                    }
                ],
            },
        )
        assert_true(
            invalid_create_response.status_code == 422,
            f"Timesheet create with decimal output_quantity should fail with 422, got {invalid_create_response.status_code}: {invalid_create_response.text}",
        )

        marker = f"V2-TS-{timestamp_tag()}"
        selected_workers = available_workers[:2] if len(available_workers) >= 2 else available_workers[:1]
        entries = []
        for index, worker in enumerate(selected_workers, start=1):
            entries.append(
                {
                    "contract_record_id": int(worker["contract_record_id"]),
                    "user_id": int(worker["user_id"]),
                    "work_type": work_type,
                    "output_quantity": index + 2,
                    "customer_duration_hours": f"{Decimal('1.25') * index:.2f}",
                    "candidate_duration_hours": f"{Decimal('1.00') * index:.2f}",
                    "role_name": role_name,
                    "non_operational_duration_hours": f"{Decimal('0.50') * index:.2f}",
                    "note_asset_ids": [note_asset_id] if index == 1 else [],
                    "extra_notes": f"{marker}-extra-{index}",
                    "poc_evaluation": f"{marker}-poc-{index}",
                }
            )
        create_result = await fetch_json(
            client,
            "POST",
            f"/v1/timesheets/companies/{company_id}/projects/{project_id}/records/batch",
            headers=headers,
            expected_status=201,
            json={
                "sub_project_name": marker,
                "work_date": date.today().isoformat(),
                "language": language,
                "project_link": f"https://example.com/{marker.lower()}",
                "human_efficiency_minutes": 5,
                "team_leader_user_id": leader_user_id,
                "entries": entries,
            },
        )
        assert_true(
            int(create_result.get("created_count") or 0) == len(entries),
            f"Expected {len(entries)} timesheet records to be created, got {create_result.get('created_count')}.",
        )

        filtered_workspace = await fetch_json(
            client,
            "GET",
            f"/v1/timesheets/companies/{company_id}/projects/{project_id}/workspace",
            headers=headers,
            params={"start_date": date.today().isoformat(), "end_date": date.today().isoformat()},
        )
        created_records = [
            item for item in filtered_workspace.get("records", []) if str(item.get("sub_project_name")) == marker
        ]
        assert_true(
            len(created_records) == len(entries),
            f"Expected {len(entries)} created timesheet records for marker {marker}, got {len(created_records)}.",
        )
        created_record_ids = [int(item["id"]) for item in created_records]

        updated_record = created_records[0]
        updated_work_type = str(
            (workspace_payload.get("timesheet_work_types") or [work_type])[1]
            if len(workspace_payload.get("timesheet_work_types") or []) > 1
            else work_type
        )
        updated_role = str(
            (workspace_payload.get("timesheet_roles") or [role_name])[1]
            if len(workspace_payload.get("timesheet_roles") or []) > 1
            else role_name
        )
        update_result = await fetch_json(
            client,
            "PATCH",
            f"/v1/timesheets/companies/{company_id}/projects/{project_id}/records/{updated_record['id']}",
            headers=headers,
            json={
                "sub_project_name": marker,
                "work_date": date.today().isoformat(),
                "language": language,
                "project_link": f"https://example.com/{marker.lower()}",
                "human_efficiency_minutes": 6,
                "team_leader_user_id": leader_user_id,
                "contract_record_id": int(updated_record["contract_record_id"]),
                "user_id": int(updated_record["user_id"]),
                "work_type": updated_work_type,
                "output_quantity": 9,
                "customer_duration_hours": "3.75",
                "candidate_duration_hours": "2.25",
                "role_name": updated_role,
                "non_operational_duration_hours": "0.25",
                "note_asset_ids": [note_asset_id],
                "extra_notes": f"{marker}-updated-extra",
                "poc_evaluation": f"{marker}-updated-poc",
            },
        )
        assert_true(
            quantize_decimal(update_result.get("output_quantity")) == Decimal("9.00"),
            "Timesheet update did not persist output quantity.",
        )
        assert_true(
            str(update_result.get("extra_notes") or "") == f"{marker}-updated-extra",
            "Timesheet update did not persist extra notes.",
        )

        delete_result = await fetch_json(
            client,
            "POST",
            f"/v1/timesheets/companies/{company_id}/projects/{project_id}/records/batch-delete",
            headers=headers,
            json={"record_ids": created_record_ids},
        )
        assert_true(
            int(delete_result.get("deleted_count") or 0) == len(created_record_ids),
            f"Expected {len(created_record_ids)} timesheet records to be deleted, got {delete_result.get('deleted_count')}.",
        )

        deleted_workspace = await fetch_json(
            client,
            "GET",
            f"/v1/timesheets/companies/{company_id}/projects/{project_id}/workspace",
            headers=headers,
            params={"start_date": date.today().isoformat(), "end_date": date.today().isoformat()},
        )
        deleted_matches = [
            item for item in deleted_workspace.get("records", []) if str(item.get("sub_project_name")) == marker
        ]
        assert_true(not deleted_matches, f"Deleted timesheet records for marker {marker} are still visible in workspace.")
    return f"timesheet_marker={marker} created={len(entries)} deleted={len(created_record_ids)}"


async def check_progress_contract_flow_mutations(context: SuiteContext) -> str:
    assert_true(context.seed_summary is not None, "Progress contract mutation check requires seed summary.")
    progress_seed = context.seed_summary["seed_payloads"]["progress_demo"]
    target_job = next(
        (
            job
            for job in progress_seed["jobs"]
            if str(job.get("job_title") or "").startswith("Progress Demo 2 - No Assessment + Automation Pass")
        ),
        None,
    )
    assert_true(target_job is not None, "Could not locate the dedicated screening-passed progress demo job.")
    progress_candidate = progress_seed["candidate"]
    job_id = int(target_job["job_id"])
    candidate_user_id = int(progress_candidate["user_id"])

    async with httpx.AsyncClient(base_url=context.admin_base_url, timeout=30.0) as admin_client, httpx.AsyncClient(
        base_url=context.web_base_url,
        timeout=30.0,
    ) as web_client:
        admin_token = await login_admin(
            admin_client,
            username_or_email=DEFAULT_FLOW_ADMIN_USERNAME,
            password=DEFAULT_FLOW_ADMIN_PASSWORD,
        )
        candidate_token = await login_candidate(
            web_client,
            email=str(progress_candidate.get("email") or DEFAULT_PROGRESS_CANDIDATE_EMAIL),
            password=str(progress_candidate.get("password") or DEFAULT_PROGRESS_CANDIDATE_PASSWORD),
        )
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        candidate_headers = {"Authorization": f"Bearer {candidate_token}"}

        marker = f"V2-PROGRESS-{timestamp_tag()}"

        async def fetch_candidate_progress_item() -> dict[str, Any]:
            progress_payload = await fetch_json(admin_client, "GET", f"/v1/jobs/{job_id}/progress", headers=admin_headers)
            progress_item = next(
                (item for item in progress_payload.get("items", []) if int(item.get("user_id") or 0) == candidate_user_id),
                None,
            )
            assert_true(
                progress_item is not None,
                f"Could not find progress record for candidate user_id={candidate_user_id} under job {job_id}.",
            )
            return progress_item

        progress_item = await fetch_candidate_progress_item()
        progress_id = int(progress_item["id"])
        current_stage = str(progress_item.get("current_stage") or "")
        assert_true(
            current_stage in {"screening_passed", "contract_pool", "active"},
            f"Unexpected progress stage for contract flow check: {current_stage}",
        )

        if current_stage != "active":
            contract_update_payload = await fetch_json(
                admin_client,
                "PATCH",
                f"/v1/jobs/{job_id}/progress/contract-record",
                headers=admin_headers,
                json={
                    "progress_ids": [progress_id],
                    "ensure_contract_record": True,
                    "agreement_ref_no": marker,
                    "rate": "12.34",
                },
            )
            updated_item = (contract_update_payload.get("items") or [{}])[0].get("contract_record_data") or {}
            assert_true(
                str(updated_item.get("agreement_ref_no") or "") == marker,
                "Progress contract record update did not persist the agreement ref no.",
            )

            progress_item = await fetch_candidate_progress_item()
            contract_record = progress_item.get("contract_record_data") or {}

            if not contract_record.get("draft_contract_attachment"):
                draft_upload_response = await admin_client.post(
                    f"/v1/jobs/{job_id}/progress/contract-draft/upload",
                    headers=admin_headers,
                    data={"progress_id": str(progress_id)},
                    files={
                        "file": (
                            f"{marker}-draft.pdf",
                            build_minimal_pdf_bytes(f"Draft contract for {marker}"),
                            "application/pdf",
                        )
                    },
                )
                assert_true(
                    draft_upload_response.status_code == 201,
                    f"Draft contract upload should succeed, got {draft_upload_response.status_code}: {draft_upload_response.text}",
                )

            progress_item = await fetch_candidate_progress_item()
            contract_record = progress_item.get("contract_record_data") or {}

            if not contract_record.get("candidate_signed_contract_attachment"):
                signed_upload_response = await web_client.post(
                    f"/jobs/{job_id}/signed-contract/upload",
                    headers=candidate_headers,
                    files={
                        "file": (
                            f"{marker}-signed.docx",
                            build_minimal_docx_bytes(f"Signed contract for {marker}"),
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        )
                    },
                )
                assert_true(
                    signed_upload_response.status_code == 201,
                    f"Candidate signed contract upload should succeed, got {signed_upload_response.status_code}: {signed_upload_response.text}",
                )
                signed_payload = signed_upload_response.json()
                assert_true(
                    str(signed_payload.get("current_stage")) == "contract_pool",
                    f"Candidate signed contract upload should move the record into contract_pool, got {signed_payload.get('current_stage')}.",
                )
                assert_true(
                    str((signed_payload.get("contract_record_data") or {}).get("contract_review") or "") == "待审核",
                    "Candidate signed contract upload should reset contract review to 待审核.",
                )

            progress_item = await fetch_candidate_progress_item()
            contract_record = progress_item.get("contract_record_data") or {}

            if not contract_record.get("company_sealed_contract_attachment"):
                if str(contract_record.get("contract_review") or "") != "审核通过":
                    blocked_company_sealed_response = await admin_client.post(
                        f"/v1/jobs/{job_id}/progress/company-sealed-contract/upload",
                        headers=admin_headers,
                        data={"progress_id": str(progress_id)},
                        files={
                            "file": (
                                f"{marker}-company-before-review.pdf",
                                build_minimal_pdf_bytes(f"Blocked company sealed contract for {marker}"),
                                "application/pdf",
                            )
                        },
                    )
                    assert_true(
                        blocked_company_sealed_response.status_code == 400,
                        "Company sealed upload before review approval should be blocked.",
                    )
                    assert_true(
                        "review is approved" in blocked_company_sealed_response.text,
                        f"Unexpected company sealed pre-review rejection: {blocked_company_sealed_response.text}",
                    )

                    contract_review_payload = await fetch_json(
                        admin_client,
                        "PATCH",
                        f"/v1/jobs/{job_id}/progress/contract-record",
                        headers=admin_headers,
                        json={
                            "progress_ids": [progress_id],
                            "contract_review": "审核通过",
                        },
                    )
                    reviewed_item = (contract_review_payload.get("items") or [{}])[0].get("contract_record_data") or {}
                    assert_true(
                        str(reviewed_item.get("contract_review") or "") == "审核通过",
                        "Contract review approval did not persist.",
                    )

                company_sealed_response = await admin_client.post(
                    f"/v1/jobs/{job_id}/progress/company-sealed-contract/upload",
                    headers=admin_headers,
                    data={"progress_id": str(progress_id)},
                    files={
                        "file": (
                            f"{marker}-company.pdf",
                            build_minimal_pdf_bytes(f"Company sealed contract for {marker}"),
                            "application/pdf",
                        )
                    },
                )
                assert_true(
                    company_sealed_response.status_code == 201,
                    f"Company sealed contract upload should succeed, got {company_sealed_response.status_code}: {company_sealed_response.text}",
                )
                company_payload = company_sealed_response.json()
                assert_true(
                    str(company_payload.get("current_stage")) == "active",
                    "Company sealed upload should move the record to active.",
                )
                assert_true(
                    str((company_payload.get("contract_record_data") or {}).get("contract_status") or "") == "Active",
                    "Company sealed upload did not activate the contract.",
                )

        progress_item = await fetch_candidate_progress_item()
        final_contract_record = progress_item.get("contract_record_data") or {}
        assert_true(
            str(progress_item.get("current_stage") or "") == "active",
            "Progress flow check should end in active stage.",
        )
        assert_true(
            bool(final_contract_record.get("company_sealed_contract_attachment")),
            "Active progress record is missing the company sealed contract attachment.",
        )
        assert_true(
            str(final_contract_record.get("contract_status") or "") == "Active",
            "Active progress record did not carry an Active contract status.",
        )

        active_progress_payload = await fetch_json(
            admin_client,
            "GET",
            f"/v1/jobs/{job_id}/progress",
            headers=admin_headers,
            params={"active_stage": "employed"},
        )
        assert_true(
            any(int(item.get("id") or 0) == progress_id for item in active_progress_payload.get("items", [])),
            "Active progress tab did not return the updated progress record.",
        )

        candidate_contracts_payload = await fetch_json(
            web_client,
            "GET",
            "/me/contracts",
            headers=candidate_headers,
            params={"page_size": 50},
        )
        candidate_contract = next(item for item in candidate_contracts_payload.get("items", []) if int(item.get("job_id") or 0) == job_id)
        assert_true(
            str(candidate_contract.get("current_stage")) == "active",
            "Candidate contract list did not reflect the active-stage contract after company signed upload.",
        )
    return (
        f"job_id={job_id} progress_id={progress_id} "
        f"final_stage={progress_item.get('current_stage')} agreement_ref={marker}"
    )


async def run_check(
    name: str,
    callback: Callable[[SuiteContext], Awaitable[str]],
    context: SuiteContext,
    results: list[dict[str, Any]],
) -> None:
    try:
        details = await callback(context)
        print_detail(f"[PASS] {name}: {details}")
        results.append({"name": name, "passed": True, "details": details})
    except Exception as exc:  # noqa: BLE001
        print_detail(f"[FAIL] {name}: {exc}")
        results.append({"name": name, "passed": False, "error": str(exc)})


async def main_async() -> int:
    args = parse_args()
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    print_step("Preflight: ensure local APIs are reachable")
    await preflight_http_endpoint(args.web_base_url.replace("/api/v1", "/docs"))
    await preflight_http_endpoint(args.admin_base_url.replace("/api", "/docs"))
    print_detail(f"candidate api ok: {args.web_base_url}")
    print_detail(f"admin api ok: {args.admin_base_url}")

    print_step("Seed: refresh or load demo data bundle")
    seed_summary = load_seed_summary(args)
    if seed_summary:
        print_detail("seed summary loaded and ready for downstream checks")
    else:
        print_detail("running in skip-seed mode with known stable accounts")

    context = SuiteContext(
        web_base_url=args.web_base_url.rstrip("/"),
        admin_base_url=args.admin_base_url.rstrip("/"),
        seed_summary=seed_summary,
    )
    results: list[dict[str, Any]] = []

    print_step("Regression: candidate and admin portal API checks")
    checks: list[tuple[str, Callable[[SuiteContext], Awaitable[str]]]] = [
        ("candidate_my_jobs_and_contract_views", check_candidate_my_jobs_and_contract_views),
        ("public_jobs_search_and_detail", check_public_jobs_search_and_detail),
        ("candidate_assessment_upload_guardrails", check_candidate_assessment_upload_guardrails),
        ("candidate_signed_contract_guardrails", check_candidate_signed_contract_guardrails),
        ("candidate_contract_asset_downloads", check_candidate_contract_asset_downloads),
        ("inactive_contract_upload_block", check_inactive_contract_upload_block),
        ("candidate_timesheet_dashboard", check_candidate_timesheet_dashboard),
        ("candidate_referrals_and_earnings", check_candidate_referrals_and_earnings),
        ("admin_referrals_and_payment_filters", check_admin_referrals_and_payment_filters),
        ("admin_contracts_and_talents_queries", check_admin_contracts_and_talents_queries),
        ("admin_timesheet_and_progress_endpoints", check_admin_timesheet_and_progress_endpoints),
        ("admin_payment_record_mutations", check_admin_payment_record_mutations),
        ("admin_referral_mark_paid_mutation", check_admin_referral_mark_paid_mutation),
        ("admin_contract_mutations", check_admin_contract_mutations),
        ("admin_timesheet_mutations", check_admin_timesheet_mutations),
        ("progress_contract_flow_mutations", check_progress_contract_flow_mutations),
    ]
    for name, callback in checks:
        await run_check(name, callback, context, results)

    advanced_filter_result: dict[str, Any] | None = None
    if args.include_advanced_filter_bulk:
        print_step("Optional: advanced filter bulk regression")
        try:
            bulk_run = run_module("src.scripts.run_advanced_filter_bulk_demo", log_prefix="v2-regression-advanced-filters")
            advanced_filter_result = {
                "passed": True,
                "log_path": bulk_run.log_path,
                "stdout_tail": bulk_run.stdout.strip().splitlines()[-12:],
            }
            print_detail(f"[PASS] advanced_filter_bulk_regression: {bulk_run.log_path}")
        except Exception as exc:  # noqa: BLE001
            advanced_filter_result = {"passed": False, "error": str(exc)}
            print_detail(f"[FAIL] advanced_filter_bulk_regression: {exc}")

    report = {
        "generated_at": timestamp_tag(),
        "web_base_url": context.web_base_url,
        "admin_base_url": context.admin_base_url,
        "seed_summary_available": bool(seed_summary),
        "checks": results,
        "advanced_filter_bulk_regression": advanced_filter_result,
    }
    report_path = TMP_DIR / f"api-regression-v2-{timestamp_tag()}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    passed_count = sum(1 for item in results if item.get("passed"))
    failed_count = sum(1 for item in results if not item.get("passed"))
    print_step("Summary")
    print_detail(f"passed={passed_count} failed={failed_count}")
    print_detail(f"report={report_path}")
    if advanced_filter_result is not None:
        print_detail(f"advanced_filter_bulk_passed={advanced_filter_result.get('passed')}")

    return 1 if failed_count else 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
