from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..admin.admin_user.model import AdminUser
from ..admin.mail_account.model import MailAccount
from ..admin.mail_signature.model import MailSignature
from ..admin.mail_template.model import MailTemplate
from ..referral_bonus_model.service import ensure_referral_bonus_model
from .const import (
    JOB_DATA_ASSESSMENT_EXTERNAL_URL_KEY,
    JOB_DATA_REJECTION_MAIL_CONFIG_KEY,
)
from .model import Job
from .policy import ensure_job_editable
from .queries import (
    _ensure_form_template_exists,
    _get_admin_display_name,
    _get_company_name_map,
    _get_enabled_admin_user,
    _get_project_name_map,
    _get_referral_bonus_model_name_map,
    _resolve_company_snapshot,
    _resolve_project_selection,
    get_job_model,
)
from .schema import JobCreate, JobUpdate
from .serialization import _job_data_from_payload, _merge_job_data, serialize_job


def _ensure_owner_transfer_mail_config(
    *,
    owner_is_changing: bool,
    job: Job,
    payload: JobUpdate,
) -> None:
    if not owner_is_changing:
        return
    current_rejection_config = (job.data or {}).get(JOB_DATA_REJECTION_MAIL_CONFIG_KEY) or {}
    if payload.assessment_config is None and job.assessment_enabled:
        raise BadRequestException(
            "Owner transfer requires an assessment mail configuration owned by the new owner, "
            "or assessment must be disabled."
        )
    if (
        payload.rejection_mail_config is None
        and isinstance(current_rejection_config, dict)
        and current_rejection_config.get("enabled")
    ):
        raise BadRequestException(
            "Owner transfer requires a rejection mail configuration owned by the new owner, "
            "or rejection mail must be disabled."
        )


async def _ensure_mail_dependencies_exist(
    *,
    enabled: bool,
    mail_account_id: int | None,
    mail_template_id: int | None,
    mail_signature_id: int | None,
    config_label: str,
    db: AsyncSession,
    admin_user_id: int,
) -> None:
    if not enabled:
        return

    account_result = await db.execute(
        select(MailAccount.id).where(
            MailAccount.id == mail_account_id,
            MailAccount.admin_user_id == admin_user_id,
            MailAccount.is_deleted.is_(False),
        )
    )
    if account_result.scalar_one_or_none() is None:
        raise NotFoundException("Mail account not found.")

    template_result = await db.execute(
        select(MailTemplate.id)
        .outerjoin(AdminUser, AdminUser.id == MailTemplate.admin_user_id)
        .where(
            MailTemplate.id == mail_template_id,
            MailTemplate.is_deleted.is_(False),
            or_(
                MailTemplate.admin_user_id == admin_user_id,
                AdminUser.is_superuser.is_(True),
            ),
        )
    )
    if template_result.scalar_one_or_none() is None:
        raise NotFoundException(f"{config_label} mail template not found.")

    signature_result = await db.execute(
        select(MailSignature.id).where(
            MailSignature.id == mail_signature_id,
            MailSignature.admin_user_id == admin_user_id,
            MailSignature.is_deleted.is_(False),
        )
    )
    if signature_result.scalar_one_or_none() is None:
        raise NotFoundException(f"{config_label} mail signature not found.")


async def create_job(
    payload: JobCreate,
    db: AsyncSession,
    *,
    current_admin: dict[str, Any],
) -> dict[str, Any]:
    owner_admin_user_id = int(payload.owner_admin_user_id or current_admin["id"])
    owner_admin_user = await _get_enabled_admin_user(owner_admin_user_id, db)
    await _ensure_form_template_exists(payload.form_strategy.template_id, db)
    company_id = await _resolve_company_snapshot(
        company_id=payload.company_id,
        db=db,
    )
    project_id = await _resolve_project_selection(
        company_id=company_id,
        project_id=payload.project_id,
        db=db,
    )
    referral_bonus_model = await ensure_referral_bonus_model(
        model_id=payload.referral_bonus_model_id,
        db=db,
        active_only=True,
    )
    await _ensure_mail_dependencies_exist(
        enabled=payload.assessment_config.enabled,
        mail_account_id=payload.assessment_config.mail_account_id,
        mail_template_id=payload.assessment_config.mail_template_id,
        mail_signature_id=payload.assessment_config.mail_signature_id,
        config_label="Assessment",
        db=db,
        admin_user_id=owner_admin_user_id,
    )
    await _ensure_mail_dependencies_exist(
        enabled=payload.rejection_mail_config.enabled,
        mail_account_id=payload.rejection_mail_config.mail_account_id,
        mail_template_id=payload.rejection_mail_config.mail_template_id,
        mail_signature_id=payload.rejection_mail_config.mail_signature_id,
        config_label="Rejection",
        db=db,
        admin_user_id=owner_admin_user_id,
    )

    owner_name = payload.owner_name or _get_admin_display_name(owner_admin_user)
    data = _job_data_from_payload(payload, owner_name=owner_name)
    data["assessment_mail_account_label"] = payload.assessment_config.mail_account_label
    data["assessment_mail_template_name"] = payload.assessment_config.mail_template_name
    data["assessment_mail_signature_name"] = payload.assessment_config.mail_signature_name
    data[JOB_DATA_ASSESSMENT_EXTERNAL_URL_KEY] = payload.assessment_config.assessment_external_url
    data[JOB_DATA_REJECTION_MAIL_CONFIG_KEY] = payload.rejection_mail_config.model_dump()

    job = Job(
        title=payload.title,
        company_id=company_id,
        project_id=project_id,
        referral_bonus_model_id=referral_bonus_model.id,
        country=payload.country,
        status=payload.status,
        work_mode=payload.work_mode,
        compensation_min=payload.compensation_min,
        compensation_max=payload.compensation_max,
        compensation_unit=payload.compensation_unit,
        description=payload.description,
        owner_admin_user_id=owner_admin_user_id,
        form_template_id=payload.form_strategy.template_id,
        assessment_enabled=payload.assessment_config.enabled,
        assessment_mail_account_id=payload.assessment_config.mail_account_id,
        assessment_mail_template_id=payload.assessment_config.mail_template_id,
        assessment_mail_signature_id=payload.assessment_config.mail_signature_id,
        applicant_count=0,
        data=data,
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)
    company_name_map = await _get_company_name_map(db, [job.company_id])
    project_name_map = await _get_project_name_map(db, [job.project_id])
    return serialize_job(
        job,
        owner_name,
        company_name_map.get(job.company_id, "-"),
        project_name_map.get(job.project_id, "-"),
        referral_bonus_model.name,
        current_admin=current_admin,
    )


async def update_job(
    job_id: int,
    payload: JobUpdate,
    db: AsyncSession,
    *,
    current_admin: dict[str, Any],
) -> dict[str, Any]:
    job = await get_job_model(job_id, db)
    ensure_job_editable(job, current_admin)
    next_company_id = job.company_id
    next_project_id = job.project_id
    next_owner_admin_user_id = int(job.owner_admin_user_id)
    next_owner_admin_user: AdminUser | None = None
    if payload.owner_admin_user_id is not None:
        next_owner_admin_user = await _get_enabled_admin_user(payload.owner_admin_user_id, db)
        next_owner_admin_user_id = int(next_owner_admin_user.id)
    owner_is_changing = next_owner_admin_user_id != int(job.owner_admin_user_id)

    _ensure_owner_transfer_mail_config(owner_is_changing=owner_is_changing, job=job, payload=payload)

    if payload.form_strategy is not None:
        await _ensure_form_template_exists(payload.form_strategy.template_id, db)
        job.form_template_id = payload.form_strategy.template_id

    if payload.assessment_config is not None:
        await _ensure_mail_dependencies_exist(
            enabled=payload.assessment_config.enabled,
            mail_account_id=payload.assessment_config.mail_account_id,
            mail_template_id=payload.assessment_config.mail_template_id,
            mail_signature_id=payload.assessment_config.mail_signature_id,
            config_label="Assessment",
            db=db,
            admin_user_id=next_owner_admin_user_id,
        )
        job.assessment_enabled = payload.assessment_config.enabled
        job.assessment_mail_account_id = (
            payload.assessment_config.mail_account_id if payload.assessment_config.enabled else None
        )
        job.assessment_mail_template_id = (
            payload.assessment_config.mail_template_id if payload.assessment_config.enabled else None
        )
        job.assessment_mail_signature_id = (
            payload.assessment_config.mail_signature_id if payload.assessment_config.enabled else None
        )

    if payload.rejection_mail_config is not None:
        await _ensure_mail_dependencies_exist(
            enabled=payload.rejection_mail_config.enabled,
            mail_account_id=payload.rejection_mail_config.mail_account_id,
            mail_template_id=payload.rejection_mail_config.mail_template_id,
            mail_signature_id=payload.rejection_mail_config.mail_signature_id,
            config_label="Rejection",
            db=db,
            admin_user_id=next_owner_admin_user_id,
        )

    if payload.company_id is not None:
        next_company_id = await _resolve_company_snapshot(
            company_id=payload.company_id,
            db=db,
        )

    if payload.project_id is not None:
        next_project_id = await _resolve_project_selection(
            company_id=next_company_id,
            project_id=payload.project_id,
            db=db,
        )
    elif payload.company_id is not None and next_company_id != job.company_id:
        raise BadRequestException("Project selection is required when company changes.")

    if payload.referral_bonus_model_id is not None and int(payload.referral_bonus_model_id) != int(
        job.referral_bonus_model_id
    ):
        referral_bonus_model = await ensure_referral_bonus_model(
            model_id=payload.referral_bonus_model_id,
            db=db,
            active_only=True,
        )
        job.referral_bonus_model_id = referral_bonus_model.id

    if payload.title is not None:
        job.title = payload.title
    if payload.company_id is not None:
        job.company_id = next_company_id
    if payload.project_id is not None:
        job.project_id = next_project_id
    if payload.country is not None:
        job.country = payload.country
    if payload.status is not None:
        job.status = payload.status
    if payload.work_mode is not None:
        job.work_mode = payload.work_mode
    if payload.compensation_unit is not None:
        job.compensation_unit = payload.compensation_unit
    if payload.description is not None:
        job.description = payload.description
    if payload.owner_admin_user_id is not None:
        job.owner_admin_user_id = next_owner_admin_user_id
    if "compensation_min" in payload.model_fields_set:
        job.compensation_min = payload.compensation_min
    if "compensation_max" in payload.model_fields_set:
        job.compensation_max = payload.compensation_max

    next_owner_name = payload.owner_name
    if next_owner_name is None:
        next_owner_name = (
            _get_admin_display_name(next_owner_admin_user)
            if next_owner_admin_user is not None
            else (job.data or {}).get("owner_name")
        ) or str(current_admin.get("name") or current_admin.get("username") or "")

    next_data = _merge_job_data(job.data or {}, payload, owner_name=next_owner_name)
    if payload.assessment_config is not None:
        next_data["assessment_mail_account_label"] = payload.assessment_config.mail_account_label
        next_data["assessment_mail_template_name"] = payload.assessment_config.mail_template_name
        next_data["assessment_mail_signature_name"] = payload.assessment_config.mail_signature_name
        next_data[JOB_DATA_ASSESSMENT_EXTERNAL_URL_KEY] = payload.assessment_config.assessment_external_url
    if payload.rejection_mail_config is not None:
        next_data[JOB_DATA_REJECTION_MAIL_CONFIG_KEY] = payload.rejection_mail_config.model_dump()
    job.data = next_data

    job.updated_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(job)
    company_name_map = await _get_company_name_map(db, [job.company_id])
    project_name_map = await _get_project_name_map(db, [job.project_id])
    referral_bonus_model_name_map = await _get_referral_bonus_model_name_map(db, [job.referral_bonus_model_id])
    return serialize_job(
        job,
        next_owner_name,
        company_name_map.get(job.company_id, "-"),
        project_name_map.get(job.project_id, "-"),
        referral_bonus_model_name_map.get(job.referral_bonus_model_id),
        current_admin=current_admin,
    )
