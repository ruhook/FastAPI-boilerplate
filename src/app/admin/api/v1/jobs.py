from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.db.database import async_get_db
from ....modules.admin.role.const import is_assessment_reviewer_only_permissions
from ....modules.job.schema import JobCreate, JobListPage, JobOwnerOptionRead, JobRead, JobUpdate
from ....modules.job.service import (
    create_job,
    ensure_job_editable_for_admin,
    get_job_for_admin,
    list_job_owner_options,
    list_jobs,
    update_job,
)
from ....modules.job_progress.const import RecruitmentStage
from ....modules.job_progress.schema import (
    JobProgressAssessmentAutomationRequest,
    JobProgressAssessmentAutomationResponse,
    JobProgressAssessmentInviteMarkRequest,
    JobProgressAssessmentInviteMarkResponse,
    JobProgressAssessmentReviewUpdateRequest,
    JobProgressAssessmentReviewUpdateResponse,
    JobProgressCompanySealedContractUploadResponse,
    JobProgressContractDraftUploadResponse,
    JobProgressContractRecordUpdateRequest,
    JobProgressContractRecordUpdateResponse,
    JobProgressLanguageUpdateRequest,
    JobProgressLanguageUpdateResponse,
    JobProgressListPage,
    JobProgressNoteUpdateRequest,
    JobProgressNoteUpdateResponse,
    JobProgressNotifySignContractRequest,
    JobProgressNotifySignContractResponse,
    JobProgressOnboardingUpdateRequest,
    JobProgressOnboardingUpdateResponse,
    JobProgressStageMoveRequest,
    JobProgressStageMoveResponse,
)
from ....modules.job_progress.service import (
    execute_job_progress_assessment_automation,
    list_job_progress,
    mark_job_progress_assessment_invited,
    move_job_progress_stage,
    notify_job_progress_sign_contract,
    update_job_progress_assessment_review,
    update_job_progress_contract_record,
    update_job_progress_language,
    update_job_progress_note,
    update_job_progress_onboarding,
    upload_job_progress_company_sealed_contract,
    upload_job_progress_contract_draft,
)
from ..dependencies import get_current_admin_user, require_admin_permission, require_any_admin_permission

router = APIRouter(prefix="/jobs", tags=["admin-jobs"])


def _is_assessment_reviewer_only(current_admin: dict[str, Any]) -> bool:
    return is_assessment_reviewer_only_permissions(
        current_admin.get("permissions") or [],
        is_superuser=bool(current_admin.get("is_superuser")),
    )


async def _ensure_job_write_allowed(
    job_id: int,
    db: AsyncSession,
    current_admin: dict[str, Any],
    *,
    allow_assessment_reviewer: bool = False,
) -> None:
    if allow_assessment_reviewer and _is_assessment_reviewer_only(current_admin):
        return
    await ensure_job_editable_for_admin(job_id, db, current_admin=current_admin)


@router.get(
    "",
    response_model=JobListPage,
    dependencies=[Depends(require_any_admin_permission("岗位管理", "测试题判题"))],
)
async def read_jobs(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    keyword: str | None = None,
    status: str | None = None,
    company_id: int | None = Query(default=None, ge=1),
    country: str | None = None,
    sort_by: str | None = Query(default=None),
    sort_order: str | None = Query(default=None),
) -> dict[str, Any]:
    return await list_jobs(
        db,
        page=page,
        page_size=page_size,
        keyword=keyword,
        status=status,
        company_id=company_id,
        country=country,
        sort_by=sort_by,
        sort_order=sort_order,
        current_admin=current_admin,
    )


@router.get(
    "/owner-options",
    response_model=list[JobOwnerOptionRead],
    dependencies=[Depends(require_admin_permission("岗位管理"))],
)
async def read_job_owner_options(
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> list[dict[str, Any]]:
    return await list_job_owner_options(db)


@router.post("", response_model=JobRead, status_code=201, dependencies=[Depends(require_admin_permission("岗位管理"))])
async def create_job_endpoint(
    payload: JobCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await create_job(payload, db, current_admin=current_admin)


@router.get(
    "/{job_id}",
    response_model=JobRead,
    dependencies=[Depends(require_any_admin_permission("岗位管理", "测试题判题"))],
)
async def read_job(
    job_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await get_job_for_admin(job_id, db, current_admin=current_admin)


@router.get(
    "/{job_id}/progress",
    response_model=JobProgressListPage,
    dependencies=[Depends(require_any_admin_permission("岗位管理", "测试题判题"))],
)
async def read_job_progress(
    job_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    active_stage: str | None = Query(default=None),
    advanced_filter: str | None = Query(default=None),
) -> dict[str, Any]:
    current_stages = None
    if _is_assessment_reviewer_only(current_admin):
        current_stages = [RecruitmentStage.ASSESSMENT_REVIEW.value]
    return await list_job_progress(
        job_id=job_id,
        active_stage=active_stage,
        advanced_filter=advanced_filter,
        current_stages=current_stages,
        reviewer_admin_user_id=None,
        db=db,
    )


@router.post(
    "/{job_id}/progress/stage",
    response_model=JobProgressStageMoveResponse,
    dependencies=[Depends(require_admin_permission("岗位管理"))],
)
async def move_job_progress_stage_endpoint(
    job_id: int,
    payload: JobProgressStageMoveRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    await _ensure_job_write_allowed(job_id, db, current_admin)
    return await move_job_progress_stage(
        job_id=job_id,
        progress_ids=payload.progress_ids,
        target_stage=payload.target_stage,
        reason=payload.reason,
        admin_user_id=int(current_admin["id"]),
        reviewer_scope_admin_user_id=None,
        expected_versions=payload.expected_versions,
        db=db,
    )


@router.post(
    "/{job_id}/progress/assessment-automation",
    response_model=JobProgressAssessmentAutomationResponse,
    dependencies=[Depends(require_any_admin_permission("岗位管理", "测试题判题"))],
)
async def execute_job_progress_assessment_automation_endpoint(
    job_id: int,
    payload: JobProgressAssessmentAutomationRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    await _ensure_job_write_allowed(job_id, db, current_admin, allow_assessment_reviewer=True)
    return await execute_job_progress_assessment_automation(
        job_id=job_id,
        progress_ids=payload.progress_ids,
        admin_user_id=int(current_admin["id"]),
        reviewer_scope_admin_user_id=None,
        db=db,
    )


@router.post(
    "/{job_id}/progress/assessment-invite",
    response_model=JobProgressAssessmentInviteMarkResponse,
    dependencies=[Depends(require_admin_permission("岗位管理"))],
)
async def mark_job_progress_assessment_invited_endpoint(
    job_id: int,
    payload: JobProgressAssessmentInviteMarkRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    await _ensure_job_write_allowed(job_id, db, current_admin)
    return await mark_job_progress_assessment_invited(
        job_id=job_id,
        progress_ids=payload.progress_ids,
        mail_task_id=payload.mail_task_id,
        sent_at=payload.sent_at,
        admin_user_id=int(current_admin["id"]),
        db=db,
    )


@router.patch(
    "/{job_id}/progress/contract-record",
    response_model=JobProgressContractRecordUpdateResponse,
    dependencies=[Depends(require_admin_permission("岗位管理"))],
)
async def update_job_progress_contract_record_endpoint(
    job_id: int,
    payload: JobProgressContractRecordUpdateRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    await _ensure_job_write_allowed(job_id, db, current_admin)
    update_fields = payload.model_fields_set
    return await update_job_progress_contract_record(
        job_id=job_id,
        progress_ids=payload.progress_ids,
        ensure_contract_record=payload.ensure_contract_record,
        agreement_ref_no=payload.agreement_ref_no,
        rate=payload.rate,
        end_date=payload.end_date,
        update_agreement_ref_no="agreement_ref_no" in update_fields,
        update_rate="rate" in update_fields,
        update_end_date="end_date" in update_fields,
        admin_user_id=int(current_admin["id"]),
        db=db,
    )


@router.post(
    "/{job_id}/progress/notify-sign-contract",
    response_model=JobProgressNotifySignContractResponse,
    dependencies=[Depends(require_admin_permission("岗位管理"))],
)
async def notify_job_progress_sign_contract_endpoint(
    job_id: int,
    payload: JobProgressNotifySignContractRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    await _ensure_job_write_allowed(job_id, db, current_admin)
    return await notify_job_progress_sign_contract(
        job_id=job_id,
        progress_ids=payload.progress_ids,
        admin_user_id=int(current_admin["id"]),
        db=db,
        account_id=payload.account_id,
        template_id=payload.template_id,
        signature_id=payload.signature_id,
        subject=payload.subject,
        body_html=payload.body_html,
        cc_recipients=payload.cc_recipients,
        bcc_recipients=payload.bcc_recipients,
        attachment_asset_ids=payload.attachment_asset_ids,
        render_context=payload.render_context,
    )


@router.patch(
    "/{job_id}/progress/assessment-review",
    response_model=JobProgressAssessmentReviewUpdateResponse,
    dependencies=[Depends(require_any_admin_permission("岗位管理", "测试题判题"))],
)
async def update_job_progress_assessment_review_endpoint(
    job_id: int,
    payload: JobProgressAssessmentReviewUpdateRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    await _ensure_job_write_allowed(job_id, db, current_admin, allow_assessment_reviewer=True)
    return await update_job_progress_assessment_review(
        job_id=job_id,
        progress_ids=payload.progress_ids,
        assessment_result=payload.assessment_result,
        assessment_review_comment=payload.assessment_review_comment,
        assessment_reviewer=payload.assessment_reviewer,
        assessment_reviewer_admin_user_id=payload.assessment_reviewer_admin_user_id,
        qa_status=payload.qa_status,
        qa_feedback=payload.qa_feedback,
        admin_user_id=int(current_admin["id"]),
        reviewer_scope_admin_user_id=None,
        db=db,
    )


@router.patch(
    "/{job_id}/progress/note",
    response_model=JobProgressNoteUpdateResponse,
    dependencies=[Depends(require_admin_permission("岗位管理"))],
)
async def update_job_progress_note_endpoint(
    job_id: int,
    payload: JobProgressNoteUpdateRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    await _ensure_job_write_allowed(job_id, db, current_admin)
    return await update_job_progress_note(
        job_id=job_id,
        progress_ids=payload.progress_ids,
        note=payload.note,
        admin_user_id=int(current_admin["id"]),
        db=db,
    )


@router.patch(
    "/{job_id}/progress/language",
    response_model=JobProgressLanguageUpdateResponse,
    dependencies=[Depends(require_admin_permission("岗位管理"))],
)
async def update_job_progress_language_endpoint(
    job_id: int,
    payload: JobProgressLanguageUpdateRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    await _ensure_job_write_allowed(job_id, db, current_admin)
    return await update_job_progress_language(
        job_id=job_id,
        progress_ids=payload.progress_ids,
        language=payload.language,
        admin_user_id=int(current_admin["id"]),
        db=db,
    )


@router.patch(
    "/{job_id}/progress/onboarding",
    response_model=JobProgressOnboardingUpdateResponse,
    dependencies=[Depends(require_admin_permission("岗位管理"))],
)
async def update_job_progress_onboarding_endpoint(
    job_id: int,
    payload: JobProgressOnboardingUpdateRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    await _ensure_job_write_allowed(job_id, db, current_admin)
    update_fields = payload.model_fields_set
    return await update_job_progress_onboarding(
        job_id=job_id,
        progress_ids=payload.progress_ids,
        onboarding_status=payload.onboarding_status,
        onboarding_date=payload.onboarding_date,
        salary_confirmed_at=payload.salary_confirmed_at,
        gift_package_sent_at=payload.gift_package_sent_at,
        update_onboarding_status="onboarding_status" in update_fields,
        update_onboarding_date="onboarding_date" in update_fields,
        update_salary_confirmed_at="salary_confirmed_at" in update_fields,
        update_gift_package_sent_at="gift_package_sent_at" in update_fields,
        admin_user_id=int(current_admin["id"]),
        db=db,
    )


@router.post(
    "/{job_id}/progress/contract-draft/upload",
    response_model=JobProgressContractDraftUploadResponse,
    status_code=201,
    dependencies=[Depends(require_admin_permission("岗位管理"))],
)
async def upload_job_progress_contract_draft_endpoint(
    job_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    progress_id: int = Form(...),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    await _ensure_job_write_allowed(job_id, db, current_admin)
    return await upload_job_progress_contract_draft(
        job_id=job_id,
        progress_id=progress_id,
        upload=file,
        admin_user_id=int(current_admin["id"]),
        db=db,
    )


@router.post(
    "/{job_id}/progress/company-sealed-contract/upload",
    response_model=JobProgressCompanySealedContractUploadResponse,
    status_code=201,
    dependencies=[Depends(require_admin_permission("岗位管理"))],
)
async def upload_job_progress_company_sealed_contract_endpoint(
    job_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    progress_id: int = Form(...),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    await _ensure_job_write_allowed(job_id, db, current_admin)
    return await upload_job_progress_company_sealed_contract(
        job_id=job_id,
        progress_id=progress_id,
        upload=file,
        admin_user_id=int(current_admin["id"]),
        db=db,
    )


@router.patch("/{job_id}", response_model=JobRead, dependencies=[Depends(require_admin_permission("岗位管理"))])
async def update_job_endpoint(
    job_id: int,
    payload: JobUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    return await update_job(job_id, payload, db, current_admin=current_admin)
