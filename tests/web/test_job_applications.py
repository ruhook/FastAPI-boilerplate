from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import local_session
from src.app.modules.candidate_application.model import CandidateApplication
from src.app.modules.job_progress.model import JobProgress
from src.app.modules.talent_profile.model import TalentProfile
from src.app.modules.talent_profile_merge_log.model import TalentProfileMergeLog

from tests.helpers.talent import (
    build_application_items,
    build_form_fields,
    create_candidate_user,
    create_form_template,
    create_open_job,
    create_resume_asset,
    fetch_operation_logs,
    login_web_user,
)


pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _seed_application_flow(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> dict[str, object]:
    suffix = uuid4().hex[:8]
    fields = build_form_fields()
    template = await create_form_template(db_session, suffix=suffix, fields=fields)
    first_job = await create_open_job(
        db_session,
        suffix=suffix,
        title=f"Language Specialist {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
    )
    second_job = await create_open_job(
        db_session,
        suffix=f"{suffix}-2",
        title=f"Quality Reviewer {suffix}",
        owner_admin_user_id=int(superadmin_credentials["id"]),
        form_template_id=template.id,
        form_fields=fields,
    )
    user, password = await create_candidate_user(db_session, suffix=suffix, name="Alice First")
    auth_headers = await login_web_user(web_client, username=user.email, password=password)
    first_resume = await create_resume_asset(db_session, suffix=f"{suffix}-resume-1", original_name="alice-first.pdf")
    second_resume = await create_resume_asset(db_session, suffix=f"{suffix}-resume-2", original_name="alice-second.pdf")

    print(f"[talent-web] seeded jobs: first={first_job.id}, second={second_job.id}, user={user.id}")

    first_apply_response = await web_client.post(
        f"/api/v1/jobs/{first_job.id}/apply",
        headers=auth_headers,
        json={
            "items": build_application_items(
                full_name="Alice First",
                email=user.email,
                whatsapp="+55-1111-1111",
                nationality="Brazilian",
                country_of_residence="Brazil",
                education_status="Bachelor’s degree (completed)",
                resume_asset_id=first_resume.id,
            )
        },
    )
    assert first_apply_response.status_code == 200, first_apply_response.text
    first_apply = first_apply_response.json()
    print(f"[talent-web] first application created: {first_apply}")

    return {
        "suffix": suffix,
        "user": user,
        "auth_headers": auth_headers,
        "first_job": first_job,
        "second_job": second_job,
        "first_resume": first_resume,
        "second_resume": second_resume,
        "first_apply": first_apply,
    }


async def test_web_first_application_creates_initial_talent_snapshot(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    flow = await _seed_application_flow(web_client, db_session, superadmin_credentials)
    user = flow["user"]
    first_job = flow["first_job"]
    first_resume = flow["first_resume"]
    first_apply = flow["first_apply"]

    assert first_apply["talent_profile_created"] is True
    assert first_apply["auto_merged"] is True

    async with local_session() as assertion_session:
        applications_result = await assertion_session.execute(
            select(CandidateApplication)
            .where(CandidateApplication.user_id == user.id)
            .order_by(CandidateApplication.id.asc())
        )
        applications = list(applications_result.scalars().all())
        assert len(applications) == 1
        assert applications[0].job_id == first_job.id

        talent_result = await assertion_session.execute(select(TalentProfile).where(TalentProfile.user_id == user.id))
        talent = talent_result.scalar_one()
        assert talent.id == first_apply["talent_profile_id"]
        assert talent.full_name == "Alice First"
        assert talent.email == user.email
        assert talent.whatsapp == "+55-1111-1111"
        assert talent.education == "Bachelor’s degree (completed)"
        assert talent.resume_asset_id == first_resume.id
        assert talent.latest_applied_job_id == first_job.id
        assert talent.source_application_id == first_apply["application_id"]
        assert talent.merge_strategy == "initial_auto_merge"

        merge_logs_result = await assertion_session.execute(
            select(TalentProfileMergeLog)
            .where(TalentProfileMergeLog.talent_profile_id == talent.id)
            .order_by(TalentProfileMergeLog.id.asc())
        )
        merge_logs = list(merge_logs_result.scalars().all())
        assert len(merge_logs) == 1
        assert merge_logs[0].merge_strategy == "initial_auto_merge"
        assert merge_logs[0].application_id == first_apply["application_id"]

        progress_result = await assertion_session.execute(
            select(JobProgress).where(JobProgress.application_id == first_apply["application_id"])
        )
        progress = progress_result.scalar_one()
        assert progress.job_id == first_job.id
        assert progress.user_id == user.id
        assert progress.talent_profile_id == talent.id
        assert progress.current_stage == "pending_screening"
        assert progress.screening_mode == "manual"

        operation_logs = await fetch_operation_logs(assertion_session, user_id=user.id)
        log_types = [log.log_type for log in operation_logs]
        print(f"[talent-web] operation logs after first apply: {log_types}")
        assert log_types == [
            "candidate_application_submitted",
            "talent_profile_initial_auto_merge",
            "job_progress_created",
        ]


async def test_web_second_application_updates_latest_job_without_overwriting_initial_snapshot(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    flow = await _seed_application_flow(web_client, db_session, superadmin_credentials)
    user = flow["user"]
    auth_headers = flow["auth_headers"]
    first_apply = flow["first_apply"]
    first_job = flow["first_job"]
    first_resume = flow["first_resume"]
    second_resume = flow["second_resume"]
    second_job = flow["second_job"]

    second_apply_response = await web_client.post(
        f"/api/v1/jobs/{second_job.id}/apply",
        headers=auth_headers,
        json={
            "items": build_application_items(
                full_name="Alice Updated",
                email=user.email,
                whatsapp="+55-2222-2222",
                nationality="Brazilian",
                country_of_residence="Brazil",
                education_status="Master’s degree (completed)",
                resume_asset_id=second_resume.id,
            )
        },
    )
    assert second_apply_response.status_code == 200, second_apply_response.text
    second_apply = second_apply_response.json()
    print(f"[talent-web] second application created: {second_apply}")

    assert first_apply["talent_profile_created"] is True
    assert first_apply["auto_merged"] is True
    assert second_apply["talent_profile_created"] is False
    assert second_apply["auto_merged"] is False
    assert second_apply["talent_profile_id"] == first_apply["talent_profile_id"]

    async with local_session() as assertion_session:
        applications_result = await assertion_session.execute(
            select(CandidateApplication)
            .where(CandidateApplication.user_id == user.id)
            .order_by(CandidateApplication.id.asc())
        )
        applications = list(applications_result.scalars().all())
        assert [application.job_id for application in applications] == [first_job.id, second_job.id]

        talent_result = await assertion_session.execute(select(TalentProfile).where(TalentProfile.user_id == user.id))
        talent = talent_result.scalar_one()
        assert talent.id == first_apply["talent_profile_id"]
        assert talent.full_name == "Alice First"
        assert talent.email == user.email
        assert talent.whatsapp == "+55-1111-1111"
        assert talent.education == "Bachelor’s degree (completed)"
        assert talent.resume_asset_id == first_resume.id
        assert talent.latest_applied_job_id == second_job.id
        assert talent.latest_applied_job_title == second_job.title
        assert talent.source_application_id == first_apply["application_id"]
        assert talent.merge_strategy == "initial_auto_merge"

        merge_logs_result = await assertion_session.execute(
            select(TalentProfileMergeLog)
            .where(TalentProfileMergeLog.talent_profile_id == talent.id)
            .order_by(TalentProfileMergeLog.id.asc())
        )
        merge_logs = list(merge_logs_result.scalars().all())
        assert len(merge_logs) == 1
        assert merge_logs[0].merge_strategy == "initial_auto_merge"
        assert merge_logs[0].application_id == first_apply["application_id"]

        progress_result = await assertion_session.execute(
            select(JobProgress)
            .where(JobProgress.user_id == user.id)
            .order_by(JobProgress.id.asc())
        )
        progresses = list(progress_result.scalars().all())
        assert len(progresses) == 2
        assert [progress.current_stage for progress in progresses] == [
            "pending_screening",
            "pending_screening",
        ]

        operation_logs = await fetch_operation_logs(assertion_session, user_id=user.id)
        log_types = [log.log_type for log in operation_logs]
        print(f"[talent-web] operation logs: {log_types}")
        assert log_types == [
            "candidate_application_submitted",
            "talent_profile_initial_auto_merge",
            "job_progress_created",
            "candidate_application_submitted",
            "talent_profile_latest_application_updated",
            "job_progress_created",
        ]
