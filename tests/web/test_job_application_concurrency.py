import asyncio
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import local_session
from src.app.modules.candidate_application.model import CandidateApplication
from src.app.modules.job.model import Job
from src.app.modules.talent_profile.model import TalentProfile
from tests.helpers.talent import (
    build_application_items,
    build_form_fields,
    create_candidate_user,
    create_form_template,
    create_open_job,
    create_resume_asset,
    login_web_user,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _items(name: str, email: str, resume_asset_id: int) -> dict[str, object]:
    return {
        "items": build_application_items(
            full_name=name,
            email=email,
            whatsapp="+1-555-0100",
            nationality="Chinese",
            country_of_residence="China",
            education_status="Bachelor’s degree (completed)",
            resume_asset_id=resume_asset_id,
        )
    }


async def _seed_job(db: AsyncSession, owner_id: int, suffix: str) -> Job:
    fields = build_form_fields()
    for field in fields:
        if field.get("key") == "resume_attachment":
            field["type"] = "file"
    template = await create_form_template(db, suffix=suffix, fields=fields)
    return await create_open_job(
        db,
        suffix=suffix,
        title=f"Concurrent Role {suffix}",
        owner_admin_user_id=owner_id,
        form_template_id=template.id,
        form_fields=fields,
    )


async def test_same_candidate_concurrent_apply_creates_one_active_row(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    job = await _seed_job(db_session, int(superadmin_credentials["id"]), suffix)
    user, password = await create_candidate_user(db_session, suffix=suffix, name="Concurrent Candidate")
    headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix=suffix, original_name="resume.pdf")
    resume.owner_id = user.id
    resume.module = "candidate_application"
    await db_session.commit()
    payload = _items(user.name, user.email, resume.id)

    responses = await asyncio.gather(
        web_client.post(f"/api/v1/jobs/{job.id}/apply", headers=headers, json=payload),
        web_client.post(f"/api/v1/jobs/{job.id}/apply", headers=headers, json=payload),
    )

    assert sorted(response.status_code for response in responses) == [200, 400]

    async with local_session() as assertion_db:
        count = await assertion_db.scalar(
            select(func.count(CandidateApplication.id)).where(
                CandidateApplication.user_id == user.id,
                CandidateApplication.job_id == job.id,
                CandidateApplication.is_deleted.is_(False),
            )
        )
        stored_job = await assertion_db.get(Job, job.id)
        assert count == 1
        assert stored_job is not None and stored_job.applicant_count == 1


async def test_different_candidates_concurrent_apply_preserves_both_count_increments(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    job = await _seed_job(db_session, int(superadmin_credentials["id"]), suffix)
    first, first_password = await create_candidate_user(
        db_session,
        suffix=f"{suffix}a",
        name="First Candidate",
    )
    second, second_password = await create_candidate_user(
        db_session,
        suffix=f"{suffix}b",
        name="Second Candidate",
    )
    first_headers = await login_web_user(web_client, username=first.email, password=first_password)
    second_headers = await login_web_user(web_client, username=second.email, password=second_password)
    first_resume = await create_resume_asset(db_session, suffix=f"{suffix}a", original_name="first.pdf")
    second_resume = await create_resume_asset(db_session, suffix=f"{suffix}b", original_name="second.pdf")
    first_resume.owner_id = first.id
    second_resume.owner_id = second.id
    first_resume.module = second_resume.module = "candidate_application"
    await db_session.commit()

    responses = await asyncio.gather(
        web_client.post(
            f"/api/v1/jobs/{job.id}/apply",
            headers=first_headers,
            json=_items(first.name, first.email, first_resume.id),
        ),
        web_client.post(
            f"/api/v1/jobs/{job.id}/apply",
            headers=second_headers,
            json=_items(second.name, second.email, second_resume.id),
        ),
    )

    assert [response.status_code for response in responses] == [200, 200]

    async with local_session() as assertion_db:
        count = await assertion_db.scalar(
            select(func.count(CandidateApplication.id)).where(
                CandidateApplication.job_id == job.id,
                CandidateApplication.is_deleted.is_(False),
            )
        )
        stored_job = await assertion_db.get(Job, job.id)
        assert count == 2
        assert stored_job is not None and stored_job.applicant_count == 2


async def test_same_candidate_first_applies_to_different_jobs_concurrently_with_one_talent_profile(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    first_job = await _seed_job(db_session, int(superadmin_credentials["id"]), f"{suffix}a")
    second_job = await _seed_job(db_session, int(superadmin_credentials["id"]), f"{suffix}b")
    user, password = await create_candidate_user(
        db_session,
        suffix=f"{suffix}same",
        name="Concurrent Candidate",
    )
    headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix=f"{suffix}same", original_name="same.pdf")
    resume.owner_id = user.id
    resume.module = "candidate_application"
    await db_session.commit()
    payload = _items(user.name, user.email, resume.id)

    responses = await asyncio.gather(
        web_client.post(f"/api/v1/jobs/{first_job.id}/apply", headers=headers, json=payload),
        web_client.post(f"/api/v1/jobs/{second_job.id}/apply", headers=headers, json=payload),
    )

    assert [response.status_code for response in responses] == [200, 200]
    async with local_session() as assertion_db:
        application_count = await assertion_db.scalar(
            select(func.count(CandidateApplication.id)).where(
                CandidateApplication.user_id == user.id,
                CandidateApplication.job_id.in_([first_job.id, second_job.id]),
                CandidateApplication.is_deleted.is_(False),
            )
        )
        talent_count = await assertion_db.scalar(
            select(func.count(TalentProfile.id)).where(
                TalentProfile.user_id == user.id,
                TalentProfile.is_deleted.is_(False),
            )
        )
        assert application_count == 2
        assert talent_count == 1
