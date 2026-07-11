import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.config import settings
from ...core.db.database import local_session
from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..admin.mail_task.const import MAIL_TASK_DATA_RENDER_CONTEXT_KEY, MailTaskStatus
from ..admin.mail_task.model import MailTask
from ..admin.mail_task.schema import MailRecipient, MailTaskCreate
from ..admin.mail_task.service import create_mail_task
from ..admin.mail_template.service import get_mail_template_model
from ..assets.model import Asset
from ..assets.service import serialize_asset
from ..candidate_application.model import CandidateApplication
from ..contract_record.commands import upsert_contract_record_for_progress
from ..contract_record.const import ContractSigningStatus
from ..contract_record.model import ContractRecord
from ..contract_record.queries import list_current_contract_records_by_progress_ids
from ..job.const import JOB_DATA_ASSESSMENT_EXTERNAL_URL_KEY
from ..job.model import Job
from ..operation_log.const import OperationLogType
from ..operation_log.service import create_operation_log
from ..user.model import User
from .const import JobProgressDataKey, RecruitmentStage, get_recruitment_stage_cn_name
from .filtering import _build_progress_json_text_expression
from .model import JobProgress
from .schema import JobProgressContractRecordUpdateItemRead, JobProgressNotifySignContractResponse
from .serialization import _extract_contract_record_asset_ids, _serialize_contract_record_data
from .state import (
    _get_company_name_map_by_job_ids,
    _mark_assessment_invited,
    get_job_progress_models,
)

logger = logging.getLogger(__name__)


def _build_candidate_assessment_url(application_id: int | None) -> str:
    if application_id is None:
        return ""
    base_url = settings.CANDIDATE_WEB_BASE_URL.strip().rstrip("/")
    path = f"/my-jobs/{application_id}"
    return f"{base_url}{path}" if base_url else path


def _get_job_mail_context(job: Job, company_name: str | None, *, application_id: int | None = None) -> dict[str, Any]:
    job_data = job.data or {}
    resolved_company_name = (company_name or "").strip()
    assessment_external_url = str(
        job_data.get(JOB_DATA_ASSESSMENT_EXTERNAL_URL_KEY)
        or job_data.get("assessment_external_url")
        or job_data.get("assessment_link")
        or job_data.get("assessmentLink")
        or ""
    )
    assessment_link = assessment_external_url or _build_candidate_assessment_url(application_id)
    return {
        "job": {
            "title": job.title,
            "job_title": job.title,
            "assessment_link": assessment_link,
            "due_date": str(job_data.get("due_date") or job_data.get("dueDate") or ""),
        },
        "company": {
            "name": resolved_company_name,
            "company_name": resolved_company_name,
        },
    }


def _build_candidate_contract_upload_url(application_id: int | None) -> str:
    if application_id is None:
        return ""
    base_url = settings.CANDIDATE_WEB_BASE_URL.strip().rstrip("/")
    path = f"/my-jobs/{application_id}"
    return f"{base_url}{path}" if base_url else path


def _contains_contract_upload_url_variable(content: str) -> bool:
    compact = "".join((content or "").lower().split())
    return "{{contract_upload_url}}" in compact or "%7b%7bcontract_upload_url%7d%7d" in compact


def _get_stage_mail_config(job: Job, target_stage: RecruitmentStage) -> dict[str, int] | None:
    if target_stage == RecruitmentStage.ASSESSMENT_REVIEW:
        if not (
            job.assessment_enabled
            and job.assessment_mail_account_id is not None
            and job.assessment_mail_template_id is not None
            and job.assessment_mail_signature_id is not None
        ):
            return None
        return {
            "account_id": int(job.assessment_mail_account_id),
            "template_id": int(job.assessment_mail_template_id),
            "signature_id": int(job.assessment_mail_signature_id),
        }

    if target_stage == RecruitmentStage.REJECTED:
        raw_config = (job.data or {}).get("rejection_mail_config") or {}
        if not isinstance(raw_config, dict) or not raw_config.get("enabled"):
            return None

        account_id = raw_config.get("mail_account_id")
        template_id = raw_config.get("mail_template_id")
        signature_id = raw_config.get("mail_signature_id")
        if account_id is None or template_id is None or signature_id is None:
            return None

        return {
            "account_id": int(account_id),
            "template_id": int(template_id),
            "signature_id": int(signature_id),
        }

    return None


async def _record_stage_mail_operation(
    *,
    job: Job,
    application: CandidateApplication,
    target_stage: RecruitmentStage,
    db: AsyncSession,
    log_type: OperationLogType,
    reason: str | None = None,
    mail_task_id: int | None = None,
) -> None:
    await create_operation_log(
        db=db,
        user_id=application.user_id,
        job_id=job.id,
        application_id=application.id,
        log_type=log_type.value,
        data={
            "job_id": job.id,
            "job_title": job.title,
            "target_stage": target_stage.value,
            "target_stage_cn_name": get_recruitment_stage_cn_name(target_stage.value),
            "reason": reason or "",
            "mail_task_id": mail_task_id,
        },
    )


async def _trigger_stage_mail_task(
    *,
    job: Job,
    application: CandidateApplication,
    target_stage: RecruitmentStage,
    db: AsyncSession,
    progress: JobProgress | None = None,
) -> None:
    mail_config = _get_stage_mail_config(job, target_stage)
    if mail_config is None:
        if target_stage in {RecruitmentStage.ASSESSMENT_REVIEW, RecruitmentStage.REJECTED}:
            await _record_stage_mail_operation(
                job=job,
                application=application,
                target_stage=target_stage,
                db=db,
                log_type=OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_SKIPPED,
                reason="mail_config_missing_or_disabled",
            )
        return

    user_result = await db.execute(
        select(User).where(
            User.id == application.user_id,
            User.is_deleted.is_(False),
        )
    )
    candidate = user_result.scalar_one_or_none()
    candidate_email = (candidate.email if candidate is not None else None) or ""
    candidate_email = candidate_email.strip()
    if not candidate_email:
        logger.warning(
            "Skip auto mail because candidate email is empty",
            extra={
                "job_id": job.id,
                "application_id": application.id,
                "target_stage": target_stage.value,
            },
        )
        await _record_stage_mail_operation(
            job=job,
            application=application,
            target_stage=target_stage,
            db=db,
            log_type=OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_SKIPPED,
            reason="candidate_email_empty",
        )
        return

    candidate_name = (candidate.name if candidate is not None else None) or candidate_email

    try:
        template = await get_mail_template_model(
            mail_config["template_id"],
            db,
            admin_user_id=job.owner_admin_user_id,
            include_public=True,
        )
        company_name_map = await _get_company_name_map_by_job_ids(job_ids=[job.id], db=db)
        render_context = _get_job_mail_context(job, company_name_map.get(job.id), application_id=application.id)
        if target_stage == RecruitmentStage.ASSESSMENT_REVIEW and progress is not None:
            render_context["job_progress"] = {
                "id": progress.id,
                "purpose": "assessment_invite",
            }
        render_context["candidate"] = {
            "name": candidate_name,
            "candidate_name": candidate_name,
            "email": candidate_email,
            "candidate_email": candidate_email,
        }
        mail_task = await create_mail_task(
            MailTaskCreate(
                account_id=mail_config["account_id"],
                template_id=mail_config["template_id"],
                signature_id=mail_config["signature_id"],
                subject=template.subject_template,
                body_html=template.body_html,
                to_recipients=[MailRecipient(name=candidate_name, email=candidate_email)],
                render_context=render_context,
            ),
            db,
            admin_user_id=job.owner_admin_user_id,
        )
        mail_task_id = int(mail_task.get("id") or 0) or None
        if target_stage == RecruitmentStage.ASSESSMENT_REVIEW and progress is not None:
            _mark_assessment_invited(progress, mail_task_id=mail_task_id)
        await _record_stage_mail_operation(
            job=job,
            application=application,
            target_stage=target_stage,
            db=db,
            log_type=OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_CREATED,
            mail_task_id=mail_task_id,
        )
    except Exception:
        logger.exception(
            "Failed to create automatic stage mail task",
            extra={
                "job_id": job.id,
                "application_id": application.id,
                "target_stage": target_stage.value,
            },
        )
        await _record_stage_mail_operation(
            job=job,
            application=application,
            target_stage=target_stage,
            db=db,
            log_type=OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_FAILED,
            reason="create_mail_task_failed",
        )


async def sync_assessment_sent_at_from_mail_task(mail_task_id: int) -> bool:
    async with local_session() as db:
        task = await db.get(MailTask, mail_task_id)
        if task is None or task.status != MailTaskStatus.SENT.value or task.sent_at is None:
            return False

        task_data = task.data or {}
        render_context = task_data.get(MAIL_TASK_DATA_RENDER_CONTEXT_KEY, {}) if isinstance(task_data, dict) else {}
        job_progress_context = render_context.get("job_progress", {}) if isinstance(render_context, dict) else {}
        progress: JobProgress | None = None
        if isinstance(job_progress_context, dict) and job_progress_context.get("purpose") == "assessment_invite":
            raw_progress_id = job_progress_context.get("id")
            progress_id = 0
            if raw_progress_id is not None:
                try:
                    progress_id = int(raw_progress_id)
                except (TypeError, ValueError):
                    progress_id = 0
            if progress_id:
                progress = await db.get(JobProgress, progress_id, with_for_update=True)

        if progress is None:
            mail_task_id_expr = _build_progress_json_text_expression(
                JobProgressDataKey.ASSESSMENT_INVITE_MAIL_TASK_ID.value
            )
            progress_result = await db.execute(
                select(JobProgress)
                .where(
                    JobProgress.is_deleted.is_(False),
                    mail_task_id_expr == str(mail_task_id),
                )
                .limit(1)
                .with_for_update()
            )
            progress = progress_result.scalar_one_or_none()

        if progress is None or progress.is_deleted:
            return False

        changed_fields = _mark_assessment_invited(progress, sent_at=task.sent_at, mail_task_id=mail_task_id)
        if not changed_fields:
            return False
        await db.commit()
        return True


async def notify_job_progress_sign_contract(
    *,
    job_id: int,
    progress_ids: list[int],
    admin_user_id: int,
    db: AsyncSession,
    account_id: int,
    template_id: int | None,
    signature_id: int | None,
    subject: str,
    body_html: str,
    cc_recipients: list[MailRecipient],
    bcc_recipients: list[MailRecipient],
    attachment_asset_ids: list[int],
    render_context: dict[str, Any],
) -> dict[str, Any]:
    job_result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
        )
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")

    progress_items = await get_job_progress_models(job_id=job_id, progress_ids=progress_ids, db=db)
    invalid_stage = next(
        (progress for progress in progress_items if progress.current_stage != RecruitmentStage.SCREENING_PASSED.value),
        None,
    )
    if invalid_stage is not None:
        raise BadRequestException("Sign contract notification can only be sent in 筛选通过.")

    contract_record_map = await list_current_contract_records_by_progress_ids(
        progress_ids=[progress.id for progress in progress_items],
        db=db,
    )
    missing_contract = next(
        (
            progress
            for progress in progress_items
            if progress.id not in contract_record_map
            or contract_record_map[progress.id].draft_contract_asset_id in (None, 0, "")
        ),
        None,
    )
    if missing_contract is not None:
        raise BadRequestException("Sign contract notification requires uploaded draft contracts.")

    user_result = await db.execute(
        select(User).where(
            User.id.in_([progress.user_id for progress in progress_items]),
            User.is_deleted.is_(False),
        )
    )
    user_map = {int(user.id): user for user in user_result.scalars().all()}

    def has_candidate_email(progress: JobProgress) -> bool:
        candidate = user_map.get(progress.user_id)
        return bool(candidate is not None and (candidate.email or "").strip())

    missing_email = next(
        (progress for progress in progress_items if not has_candidate_email(progress)),
        None,
    )
    if missing_email is not None:
        raise BadRequestException("Candidate email is required for sign contract notification.")

    if not _contains_contract_upload_url_variable(body_html):
        raise BadRequestException("Sign contract notification template must include {{contract_upload_url}}.")

    company_name_map = await _get_company_name_map_by_job_ids(job_ids=[job.id], db=db)
    base_render_context = {
        **_get_job_mail_context(job, company_name_map.get(job.id)),
        **(render_context or {}),
    }

    mail_task_ids: list[int] = []
    updated_contract_records: dict[int, ContractRecord] = {}
    for progress in progress_items:
        candidate = user_map.get(progress.user_id)
        if candidate is None:
            raise BadRequestException("Candidate email is required for sign contract notification.")
        candidate_email = (candidate.email or "").strip()
        candidate_name = (candidate.name or candidate_email).strip()
        contract_upload_url = _build_candidate_contract_upload_url(progress.application_id)
        task = await create_mail_task(
            MailTaskCreate(
                account_id=account_id,
                template_id=template_id,
                signature_id=signature_id,
                subject=subject,
                body_html=body_html,
                to_recipients=[MailRecipient(name=candidate_name, email=candidate_email)],
                cc_recipients=cc_recipients,
                bcc_recipients=bcc_recipients,
                attachment_asset_ids=list(dict.fromkeys(int(asset_id) for asset_id in attachment_asset_ids)),
                render_context={
                    **base_render_context,
                    "candidate_name": candidate_name,
                    "candidate_email": candidate_email,
                    "contract_upload_url": contract_upload_url,
                    "candidate": {
                        "name": candidate_name,
                        "candidate_name": candidate_name,
                        "email": candidate_email,
                        "candidate_email": candidate_email,
                    },
                    "contract": {
                        "upload_url": contract_upload_url,
                        "contract_upload_url": contract_upload_url,
                    },
                },
            ),
            db,
            admin_user_id=admin_user_id,
        )
        mail_task_id = int(task.get("id") or 0)
        if mail_task_id:
            mail_task_ids.append(mail_task_id)

        updated_contract_record = await upsert_contract_record_for_progress(
            progress=progress,
            job=job,
            db=db,
            admin_user_id=admin_user_id,
            field_updates={"signing_status": ContractSigningStatus.SENT.value},
        )
        updated_contract_records[progress.id] = updated_contract_record
        await create_operation_log(
            db=db,
            user_id=progress.user_id,
            job_id=progress.job_id,
            application_id=progress.application_id,
            talent_profile_id=progress.talent_profile_id,
            log_type=OperationLogType.JOB_PROGRESS_CONTRACT_RECORD_UPDATED.value,
            data={
                "job_progress_id": progress.id,
                "job_id": job.id,
                "job_title": job.title,
                "current_stage": progress.current_stage,
                "current_stage_cn_name": get_recruitment_stage_cn_name(progress.current_stage),
                "operator_admin_user_id": admin_user_id,
                "contract_updated_fields": ["signing_status"],
                "mail_task_id": mail_task_id or None,
            },
        )

    asset_ids: set[int] = set()
    for record in updated_contract_records.values():
        asset_ids.update(_extract_contract_record_asset_ids(record))

    asset_map: dict[int, dict[str, Any]] = {}
    if asset_ids:
        asset_result = await db.execute(
            select(Asset).where(
                Asset.id.in_(sorted(asset_ids)),
                Asset.is_deleted.is_(False),
            )
        )
        asset_map = {int(asset.id): serialize_asset(asset) for asset in asset_result.scalars().all()}

    await db.flush()

    return JobProgressNotifySignContractResponse(
        updated_count=len(progress_items),
        mail_task_ids=mail_task_ids,
        items=[
            JobProgressContractRecordUpdateItemRead(
                progress_id=progress.id,
                contract_record_data=_serialize_contract_record_data(
                    progress=progress,
                    contract_record=updated_contract_records.get(progress.id),
                    asset_map=asset_map,
                ),
            )
            for progress in progress_items
        ],
    ).model_dump()
