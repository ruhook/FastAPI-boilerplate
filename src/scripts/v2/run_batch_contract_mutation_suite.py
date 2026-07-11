from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from typing import Any

import httpx
from sqlalchemy import select

from ...app.core.db.database import async_engine, local_session
from ...app.core.security import get_password_hash
from ...app.modules.user.model import User
from ..run_client_apply_demo import (
    ensure_resume_asset,
    fetch_current_user,
    register_or_reuse_candidate,
    submit_application,
)
from ..seed_job_progress_demo_flow import (
    DEMO_JOB_DEFINITIONS,
    build_application_items,
    fetch_existing_application_record,
    reset_progress_demo_state,
    seed_admin_and_jobs,
)
from .shared import (
    DEFAULT_ADMIN_BASE_URL,
    DEFAULT_FLOW_ADMIN_PASSWORD,
    DEFAULT_FLOW_ADMIN_USERNAME,
    DEFAULT_WEB_BASE_URL,
    TMP_DIR,
    build_minimal_docx_bytes,
    build_minimal_pdf_bytes,
    ensure_status,
    login_admin,
    login_candidate,
    preflight_http_endpoint,
    print_detail,
    print_step,
    timestamp_tag,
)

DEFAULT_BATCH_PASSWORD = "Candidate123!"
DEFAULT_BATCH_SIZE = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run V2 batch-style contract mutation checks.")
    parser.add_argument("--web-base-url", default=DEFAULT_WEB_BASE_URL, help="Candidate API base URL.")
    parser.add_argument("--admin-base-url", default=DEFAULT_ADMIN_BASE_URL, help="Admin API base URL.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Number of candidates to include.")
    parser.add_argument(
        "--email-prefix",
        default="batch.contract.v2",
        help="Stable test candidate email prefix. Existing records under the progress demo job are reset first.",
    )
    return parser.parse_args()


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def normalize_match_text(value: str) -> str:
    stem = value.rsplit(".", 1)[0]
    return "".join(stem.split()).upper()


def match_files_by_contract_number(
    *,
    contract_numbers: dict[int, str],
    files: dict[str, tuple[bytes, str]],
) -> dict[int, tuple[str, bytes, str]]:
    normalized_files = [
        {
            "file_name": file_name,
            "content": content,
            "mime_type": mime_type,
            "normalized_name": normalize_match_text(file_name),
        }
        for file_name, (content, mime_type) in files.items()
    ]
    match_counts: dict[str, int] = {}
    matches: dict[int, tuple[str, bytes, str]] = {}
    ambiguous: list[str] = []
    missing: list[str] = []

    for progress_id, contract_number in contract_numbers.items():
        normalized_contract_number = normalize_match_text(contract_number)
        matched_files = [item for item in normalized_files if normalized_contract_number in item["normalized_name"]]
        if len(matched_files) != 1:
            if matched_files:
                ambiguous.append(contract_number)
            else:
                missing.append(contract_number)
            continue
        matched = matched_files[0]
        match_counts[matched["file_name"]] = match_counts.get(matched["file_name"], 0) + 1
        matches[progress_id] = (
            matched["file_name"],
            matched["content"],
            matched["mime_type"],
        )

    duplicated_files = [file_name for file_name, count in match_counts.items() if count > 1]
    if missing or ambiguous or duplicated_files:
        raise AssertionError(
            "Contract file matching failed: "
            f"missing={missing} ambiguous={ambiguous} duplicated_files={duplicated_files}"
        )
    return matches


async def ensure_candidate_password(*, email: str, password: str) -> None:
    async with local_session() as session:
        result = await session.execute(
            select(User).where(
                User.email == email.strip().lower(),
                User.is_deleted.is_(False),
            )
        )
        user = result.scalar_one_or_none()
        if user is not None:
            user.hashed_password = get_password_hash(password)
            await session.commit()


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


async def prepare_batch_candidates(
    *,
    web_base_url: str,
    batch_size: int,
    email_prefix: str,
) -> dict[str, Any]:
    seed_payload, jobs = await seed_admin_and_jobs()
    target_definition = next(item for item in DEMO_JOB_DEFINITIONS if item["key"] == "no_assessment_auto_pass")
    target_job = next(job for job in jobs if job.title == target_definition["title"])
    job_id = int(target_job.id)

    prepared_candidates: list[dict[str, Any]] = []
    async with httpx.AsyncClient(base_url=web_base_url.rstrip("/"), timeout=30.0) as client:
        for index in range(1, batch_size + 1):
            email = f"{email_prefix}.{index}@example.com".lower()
            name = f"Batch Contract V2 {index}"
            await register_or_reuse_candidate(client, name=name, email=email, password=DEFAULT_BATCH_PASSWORD)
            await ensure_candidate_password(email=email, password=DEFAULT_BATCH_PASSWORD)
            access_token = await login_candidate(client, email=email, password=DEFAULT_BATCH_PASSWORD)
            current_user = await fetch_current_user(client, access_token=access_token)
            await reset_progress_demo_state(user_id=int(current_user["id"]), job_ids=[job_id])
            resume_asset = await ensure_resume_asset(user_id=int(current_user["id"]), email=email)
            items = build_application_items(
                scenario_key=str(target_definition["key"]),
                candidate_name=name,
                candidate_email=email,
                resume_asset_id=int(resume_asset.id),
            )
            try:
                application_payload = await submit_application(
                    client,
                    access_token=access_token,
                    job_id=job_id,
                    items=items,
                )
            except RuntimeError as exc:
                if "already applied to this role" not in str(exc):
                    raise
                existing = await fetch_existing_application_record(
                    user_id=int(current_user["id"]),
                    job_id=job_id,
                )
                if existing is None:
                    raise
                application_payload = existing
            prepared_candidates.append(
                {
                    "email": email,
                    "password": DEFAULT_BATCH_PASSWORD,
                    "name": name,
                    "user_id": int(current_user["id"]),
                    "application_id": int(application_payload["application_id"]),
                    "talent_profile_id": int(application_payload["talent_profile_id"]),
                }
            )

    return {
        "admin": seed_payload["admin"],
        "job": {
            "id": job_id,
            "title": target_job.title,
            "compensation_unit": target_job.compensation_unit,
        },
        "candidates": prepared_candidates,
    }


async def get_progress_items_for_users(
    *,
    admin_client: httpx.AsyncClient,
    headers: dict[str, str],
    job_id: int,
    user_ids: set[int],
) -> list[dict[str, Any]]:
    progress_payload = await fetch_json(admin_client, "GET", f"/v1/jobs/{job_id}/progress", headers=headers)
    items = [item for item in progress_payload.get("items", []) if int(item.get("user_id") or 0) in user_ids]
    if len(items) != len(user_ids):
        found_user_ids = {int(item.get("user_id") or 0) for item in items}
        raise AssertionError(f"Missing progress rows for user ids: {sorted(user_ids - found_user_ids)}")
    return items


async def get_mail_account_id(*, admin_client: httpx.AsyncClient, headers: dict[str, str]) -> int:
    accounts = await fetch_json(admin_client, "GET", "/v1/mail/accounts", headers=headers)
    enabled_account = next((item for item in accounts if item.get("status") == "enabled"), None)
    if enabled_account is None and accounts:
        enabled_account = accounts[0]
    assert_true(enabled_account is not None, "No mail account is available for sign-contract notification.")
    return int(enabled_account["id"])


async def run_batch_contract_mutation(args: argparse.Namespace) -> dict[str, Any]:
    await preflight_http_endpoint(args.web_base_url.replace("/api/v1", "/docs"))
    await preflight_http_endpoint(args.admin_base_url.replace("/api", "/docs"))
    setup = await prepare_batch_candidates(
        web_base_url=args.web_base_url,
        batch_size=max(1, int(args.batch_size)),
        email_prefix=str(args.email_prefix),
    )
    job_id = int(setup["job"]["id"])
    user_ids = {int(item["user_id"]) for item in setup["candidates"]}

    async with (
        httpx.AsyncClient(base_url=args.admin_base_url.rstrip("/"), timeout=45.0) as admin_client,
        httpx.AsyncClient(
            base_url=args.web_base_url.rstrip("/"),
            timeout=45.0,
        ) as web_client,
    ):
        admin_token = await login_admin(
            admin_client,
            username_or_email=DEFAULT_FLOW_ADMIN_USERNAME,
            password=DEFAULT_FLOW_ADMIN_PASSWORD,
        )
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        progress_items = await get_progress_items_for_users(
            admin_client=admin_client,
            headers=admin_headers,
            job_id=job_id,
            user_ids=user_ids,
        )
        assert_true(
            all(item.get("current_stage") == "screening_passed" for item in progress_items),
            f"Prepared candidates should start at screening_passed, got {[item.get('current_stage') for item in progress_items]}",
        )

        progress_ids = [int(item["id"]) for item in progress_items]
        progress_by_user_id = {int(item["user_id"]): item for item in progress_items}
        marker = f"V2-BATCH-{timestamp_tag()}"
        contract_numbers: dict[int, str] = {}
        for index, progress in enumerate(progress_items, start=1):
            contract_number = f"{marker}-{index:02d}"
            contract_numbers[int(progress["id"])] = contract_number
            update_payload = await fetch_json(
                admin_client,
                "PATCH",
                f"/v1/jobs/{job_id}/progress/contract-record",
                headers=admin_headers,
                json={
                    "progress_ids": [int(progress["id"])],
                    "ensure_contract_record": True,
                    "agreement_ref_no": contract_number,
                    "rate": f"{12 + index}.50",
                },
            )
            item = (update_payload.get("items") or [{}])[0].get("contract_record_data") or {}
            assert_true(item.get("agreement_ref_no") == contract_number, "Agreement ref no did not persist.")

        blocked_notify = await admin_client.post(
            f"/v1/jobs/{job_id}/progress/notify-sign-contract",
            headers=admin_headers,
            json={
                "progress_ids": progress_ids,
                "account_id": await get_mail_account_id(admin_client=admin_client, headers=admin_headers),
                "template_id": None,
                "signature_id": None,
                "subject": f"{marker} sign contract",
                "body_html": "<p>Please sign your contract.</p>",
                "cc_recipients": [],
                "bcc_recipients": [],
                "attachment_asset_ids": [],
                "render_context": {},
            },
        )
        assert_true(
            blocked_notify.status_code == 400,
            f"Notify before draft upload should be blocked, got {blocked_notify.status_code}: {blocked_notify.text}",
        )

        draft_files = {
            f"draft copy {contract_number.lower()} .pdf": (
                build_minimal_pdf_bytes(f"Draft contract {contract_number}"),
                "application/pdf",
            )
            for contract_number in contract_numbers.values()
        }
        draft_matches = match_files_by_contract_number(contract_numbers=contract_numbers, files=draft_files)
        for progress_id, (file_name, content, mime_type) in draft_matches.items():
            response = await admin_client.post(
                f"/v1/jobs/{job_id}/progress/contract-draft/upload",
                headers=admin_headers,
                data={"progress_id": str(progress_id)},
                files={"file": (file_name, content, mime_type)},
            )
            assert_true(
                response.status_code == 201,
                f"Draft upload failed for progress {progress_id}: {response.status_code} {response.text}",
            )
            draft_contract_data = response.json().get("contract_record_data") or {}
            assert_true(
                str(draft_contract_data.get("effective_date") or "") == date.today().isoformat(),
                "Draft upload should set the contract effective date.",
            )

        account_id = await get_mail_account_id(admin_client=admin_client, headers=admin_headers)
        notify_payload = await fetch_json(
            admin_client,
            "POST",
            f"/v1/jobs/{job_id}/progress/notify-sign-contract",
            headers=admin_headers,
            json={
                "progress_ids": progress_ids,
                "account_id": account_id,
                "template_id": None,
                "signature_id": None,
                "subject": f"{marker} contract signing notice",
                "body_html": "<p>Please review the attached draft contract and upload the signed copy.</p>",
                "cc_recipients": [],
                "bcc_recipients": [],
                "attachment_asset_ids": [],
                "render_context": {"suite": "batch_contract_v2"},
            },
        )
        assert_true(
            int(notify_payload.get("updated_count") or 0) == len(progress_ids), "Notify updated_count mismatch."
        )
        assert_true(
            len(notify_payload.get("mail_task_ids") or []) == len(progress_ids),
            "Notify should create one mail task per candidate.",
        )
        contract_ids: dict[int, int] = {}
        for item in notify_payload.get("items", []):
            data = item.get("contract_record_data") or {}
            contract_ids[int(item["progress_id"])] = int(data["id"])
            assert_true(data.get("signing_status") == "sent", "Signing status did not update after notify.")
            assert_true(
                bool(data.get("draft_contract_attachment")), "Notify response is missing draft contract attachment."
            )

        for candidate in setup["candidates"]:
            candidate_token = await login_candidate(
                web_client,
                email=str(candidate["email"]),
                password=str(candidate["password"]),
            )
            candidate_headers = {"Authorization": f"Bearer {candidate_token}"}
            progress_id = int(progress_by_user_id[int(candidate["user_id"])]["id"])
            contract_number = contract_numbers[progress_id]
            signed_response = await web_client.post(
                f"/jobs/{job_id}/signed-contract/upload",
                headers=candidate_headers,
                files={
                    "file": (
                        f"signed {contract_number.lower()}.docx",
                        build_minimal_docx_bytes(f"Signed contract {contract_number}"),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    )
                },
            )
            assert_true(
                signed_response.status_code == 201,
                f"Candidate signed upload failed for {candidate['email']}: {signed_response.status_code} {signed_response.text}",
            )
            assert_true(
                signed_response.json().get("current_stage") == "contract_pool",
                "Signed upload should move to contract_pool.",
            )

        blocked_company_response = await admin_client.post(
            f"/v1/jobs/{job_id}/progress/company-sealed-contract/upload",
            headers=admin_headers,
            data={"progress_id": str(progress_ids[0])},
            files={
                "file": (
                    f"{marker}-blocked-company.pdf",
                    build_minimal_pdf_bytes("blocked before review"),
                    "application/pdf",
                )
            },
        )
        assert_true(
            blocked_company_response.status_code == 400,
            f"Company signed upload should be blocked before review approval, got {blocked_company_response.status_code}.",
        )

        for progress_id in progress_ids:
            review_payload = await fetch_json(
                admin_client,
                "POST",
                f"/v1/contracts/{contract_ids[progress_id]}/review",
                headers=admin_headers,
                json={"target": "approved"},
            )
            assert_true(
                str(review_payload.get("contract_review_status") or "") == "approved",
                f"Contract review did not persist for progress {progress_id}.",
            )

        company_files = {
            f"company countersigned {contract_number.lower()}.pdf": (
                build_minimal_pdf_bytes(f"Company signed contract {contract_number}"),
                "application/pdf",
            )
            for contract_number in contract_numbers.values()
        }
        company_matches = match_files_by_contract_number(contract_numbers=contract_numbers, files=company_files)
        activated_progress_ids: list[int] = []
        for progress_id, (file_name, content, mime_type) in company_matches.items():
            response = await admin_client.post(
                f"/v1/jobs/{job_id}/progress/company-sealed-contract/upload",
                headers=admin_headers,
                data={"progress_id": str(progress_id)},
                files={"file": (file_name, content, mime_type)},
            )
            assert_true(
                response.status_code == 201,
                f"Company signed upload failed for progress {progress_id}: {response.status_code} {response.text}",
            )
            payload = response.json()
            assert_true(
                payload.get("current_stage") == "active", "Company signed upload should move progress to active."
            )
            contract_data = payload.get("contract_record_data") or {}
            assert_true(
                contract_data.get("contract_status") == "active", "Company signed upload should activate contract."
            )
            assert_true(
                str(contract_data.get("effective_date") or "") == date.today().isoformat(),
                "Company signed upload should preserve the draft upload effective date.",
            )
            activated_progress_ids.append(progress_id)

        active_payload = await fetch_json(
            admin_client,
            "GET",
            f"/v1/jobs/{job_id}/progress",
            headers=admin_headers,
            params={"active_stage": "employed"},
        )
        active_ids = {int(item["id"]) for item in active_payload.get("items", [])}
        assert_true(set(activated_progress_ids).issubset(active_ids), "Not all batch records appear in the active tab.")

        contracts_payload = await fetch_json(
            admin_client,
            "GET",
            "/v1/contracts",
            headers=admin_headers,
            params={"keyword": marker, "page_size": 100},
        )
        contract_items = contracts_payload.get("items", [])
        assert_true(len(contract_items) >= len(progress_ids), "Contract library did not return all batch contracts.")
        assert_true(
            all(
                item.get("contract_status") == "active"
                for item in contract_items
                if str(item.get("agreement_ref_no", "")).startswith(marker)
            ),
            "Batch contracts should all be Active in contract library.",
        )

        for candidate in setup["candidates"]:
            candidate_token = await login_candidate(
                web_client,
                email=str(candidate["email"]),
                password=str(candidate["password"]),
            )
            contracts = ensure_status(
                await web_client.get(
                    "/me/contracts",
                    headers={"Authorization": f"Bearer {candidate_token}"},
                    params={"page_size": 20},
                ),
                f"Candidate contracts failed for {candidate['email']}",
            )
            own_contract = next(
                (item for item in contracts.get("items", []) if int(item.get("job_id") or 0) == job_id),
                None,
            )
            assert_true(own_contract is not None, f"Candidate {candidate['email']} cannot see batch contract.")
            assert_true(own_contract.get("current_stage") == "active", "Candidate contract stage should be active.")

    return {
        "marker": marker,
        "job": setup["job"],
        "candidate_count": len(setup["candidates"]),
        "progress_ids": progress_ids,
        "activated_progress_ids": activated_progress_ids,
        "contract_library_matches": len(contract_items),
        "mail_task_ids": notify_payload.get("mail_task_ids") or [],
        "candidates": [
            {"email": item["email"], "password": item["password"], "user_id": item["user_id"]}
            for item in setup["candidates"]
        ],
    }


async def main_async() -> int:
    args = parse_args()
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    print_step("Batch contract mutation preflight and setup")
    try:
        report = await run_batch_contract_mutation(args)
        report_path = TMP_DIR / f"batch-contract-mutation-v2-{timestamp_tag()}.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print_detail(
            f"[PASS] batch_contract_mutation: marker={report['marker']} candidates={report['candidate_count']}"
        )
        print_detail(f"report={report_path}")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        await async_engine.dispose()


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
