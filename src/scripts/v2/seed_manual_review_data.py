from __future__ import annotations

import argparse
import json

from ..create_assessment_reviewer import (
    DEFAULT_EMAIL as DEFAULT_REVIEWER_EMAIL,
)
from ..create_assessment_reviewer import (
    DEFAULT_NAME as DEFAULT_REVIEWER_NAME,
)
from ..create_assessment_reviewer import (
    DEFAULT_ROLE_NAME as DEFAULT_REVIEWER_ROLE_NAME,
)
from .shared import (
    DEFAULT_ASSESSMENT_REVIEWER_PASSWORD,
    DEFAULT_ASSESSMENT_REVIEWER_USERNAME,
    DEFAULT_PORTAL_CANDIDATE_EMAIL,
    DEFAULT_PORTAL_CANDIDATE_PASSWORD,
    DEFAULT_PROGRESS_CANDIDATE_EMAIL,
    DEFAULT_PROGRESS_CANDIDATE_PASSWORD,
    TMP_DIR,
    extract_trailing_json,
    parse_portal_demo_log,
    print_detail,
    print_step,
    run_module,
    timestamp_tag,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prepare a consolidated manual-review dataset for local QA.")
    parser.add_argument(
        "--progress-candidate-email",
        default=DEFAULT_PROGRESS_CANDIDATE_EMAIL,
        help="Stable candidate email used by the progress demo seed.",
    )
    parser.add_argument(
        "--progress-candidate-password",
        default=DEFAULT_PROGRESS_CANDIDATE_PASSWORD,
        help="Password used by the progress demo seed.",
    )
    parser.add_argument(
        "--portal-candidate-email",
        default=DEFAULT_PORTAL_CANDIDATE_EMAIL,
        help="Stable candidate email used by the portal demo seed.",
    )
    parser.add_argument(
        "--portal-candidate-password",
        default=DEFAULT_PORTAL_CANDIDATE_PASSWORD,
        help="Password used by the portal demo seed.",
    )
    parser.add_argument(
        "--include-advanced-filter-bulk-data",
        action="store_true",
        help="Also seed the large advanced-filter bulk dataset for search stress testing.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    print_step("Step 1/5: seed candidate base dictionaries and form template")
    form_seed = run_module("src.scripts.seed_candidate_base_form_template", log_prefix="v2-seed-base-form")
    print_detail(f"candidate base form seed refreshed: {form_seed.log_path}")

    print_step("Step 2/5: seed recruitment progress manual-review data")
    progress_seed = run_module(
        "src.scripts.seed_job_progress_demo_flow",
        "--candidate-email",
        args.progress_candidate_email,
        "--candidate-password",
        args.progress_candidate_password,
        log_prefix="v2-seed-progress",
    )
    progress_payload = extract_trailing_json(progress_seed.stdout)
    print_detail(
        "progress demo ready: "
        f"admin={progress_payload['admin']['username']} "
        f"candidate={progress_payload['candidate']['email']} "
        f"jobs={len(progress_payload['jobs'])}"
    )

    print_step("Step 3/5: seed assessment reviewer account")
    reviewer_seed = run_module(
        "src.scripts.create_assessment_reviewer",
        "--reset-password",
        log_prefix="v2-seed-assessment-reviewer",
    )
    print_detail(
        f"assessment reviewer ready: username={DEFAULT_ASSESSMENT_REVIEWER_USERNAME} role={DEFAULT_REVIEWER_ROLE_NAME}"
    )

    print_step("Step 4/5: seed contracts, timesheets, earnings, and referral demo data")
    timesheet_seed = run_module("src.scripts.seed_timesheet_demo_flow", log_prefix="v2-seed-timesheets")
    timesheet_payload = extract_trailing_json(timesheet_seed.stdout)
    print_detail(
        "timesheet demo ready: "
        f"admin={timesheet_payload['admin']['username']} "
        f"viewer={timesheet_payload['candidate_portal_timesheet_viewer']['email']} "
        f"contracts={len(timesheet_payload['candidate_portal_timesheet_viewer']['contracts'])}"
    )

    print_step("Step 5/5: seed candidate-portal My Jobs / My Contracts walkthrough data")
    portal_seed = run_module(
        "src.scripts.run_candidate_my_jobs_demo",
        "--candidate-email",
        args.portal_candidate_email,
        "--candidate-password",
        args.portal_candidate_password,
        log_prefix="v2-seed-portal-demo",
    )
    portal_summary = parse_portal_demo_log(portal_seed.stdout)
    print_detail(
        "portal demo ready: "
        f"candidate={portal_summary.get('candidate_email', args.portal_candidate_email)} "
        f"fresh_job={portal_summary.get('fresh_job_title', '-')}"
    )

    advanced_filter_summary: dict[str, object] | None = None
    if args.include_advanced_filter_bulk_data:
        print_step("Optional: seed large advanced-filter bulk data")
        bulk_seed = run_module("src.scripts.run_advanced_filter_bulk_demo", log_prefix="v2-seed-advanced-filters")
        advanced_filter_summary = {
            "log_path": bulk_seed.log_path,
            "stdout_tail": bulk_seed.stdout.strip().splitlines()[-8:],
        }
        print_detail(f"advanced filter bulk seed completed: {bulk_seed.log_path}")

    summary = {
        "generated_at": timestamp_tag(),
        "manual_review_accounts": {
            "progress_admin": progress_payload["admin"],
            "progress_candidate": {
                "email": progress_payload["candidate"]["email"],
                "password": progress_payload["candidate"]["password"],
            },
            "timesheet_admin": timesheet_payload["admin"],
            "assessment_reviewer": {
                "name": DEFAULT_REVIEWER_NAME,
                "username": DEFAULT_ASSESSMENT_REVIEWER_USERNAME,
                "email": DEFAULT_REVIEWER_EMAIL,
                "password": DEFAULT_ASSESSMENT_REVIEWER_PASSWORD,
                "role": DEFAULT_REVIEWER_ROLE_NAME,
            },
            "timesheet_viewer": {
                "username": timesheet_payload["candidate_portal_timesheet_viewer"]["username"],
                "email": timesheet_payload["candidate_portal_timesheet_viewer"]["email"],
                "password": timesheet_payload["candidate_portal_timesheet_viewer"]["password"],
            },
            "portal_candidate": {
                "email": portal_summary.get("candidate_email", args.portal_candidate_email),
                "password": portal_summary.get("candidate_password", args.portal_candidate_password),
            },
        },
        "manual_review_paths": {
            "progress_jobs": [f"/jobs/{item['job_id']}/progress" for item in progress_payload["jobs"]],
            "timesheet_project_page": timesheet_payload["project"]["timesheet_page_path"],
            "candidate_working_hours": timesheet_payload["candidate_portal_timesheet_viewer"]["page_path"],
            "candidate_portal_jobs": "/jobs",
            "candidate_my_jobs": "/my-jobs",
            "candidate_my_contracts": "/my-contracts",
            "candidate_referral": "/referral",
            "candidate_earnings": "/earnings",
        },
        "seed_payloads": {
            "progress_demo": progress_payload,
            "timesheet_demo": timesheet_payload,
            "portal_demo_summary": portal_summary,
        },
        "log_files": {
            "base_form_seed": form_seed.log_path,
            "progress_seed": progress_seed.log_path,
            "assessment_reviewer_seed": reviewer_seed.log_path,
            "timesheet_seed": timesheet_seed.log_path,
            "portal_demo_seed": portal_seed.log_path,
        },
        "notes": [
            "The progress demo payload is structured JSON and is the best starting point for B-side stage / contract / mail checks.",
            "The timesheet demo payload contains active, team-leader, and terminated contracts for working-hours and earnings checks.",
            "The candidate portal demo log captures the My Jobs / My Contracts walkthrough summary for the 712696307@qq.com account.",
        ],
    }
    if progress_payload.get("superadmin") is not None:
        summary["manual_review_accounts"]["progress_superadmin"] = progress_payload["superadmin"]
    if advanced_filter_summary is not None:
        summary["advanced_filter_bulk_seed"] = advanced_filter_summary

    report_path = TMP_DIR / f"manual-review-seed-v2-{timestamp_tag()}.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print_detail(f"combined summary written: {report_path}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
