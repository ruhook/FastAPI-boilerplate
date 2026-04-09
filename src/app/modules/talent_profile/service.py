import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..assets.model import Asset
from ..admin.admin_user.model import AdminUser
from ..candidate_application.model import CandidateApplication
from ..candidate_application.const import get_candidate_application_status_cn_name
from ..candidate_application.schema import (
    CandidateApplicationSubmitRequest,
    CandidateApplicationSubmitResponse,
    CandidateApplicationSummaryRead,
)
from ..candidate_application_field_value.model import CandidateApplicationFieldValue
from ..candidate_field.const import CandidateFieldKey
from ..job.const import JOB_DATA_FORM_FIELDS_KEY, JobStatus
from ..job.model import Job
from ..job_progress.model import JobProgress
from ..job_progress.const import get_recruitment_stage_cn_name
from ..job_progress.service import create_job_progress_for_application
from ..operation_log.const import OperationLogType
from ..operation_log.model import OperationLog
from ..operation_log.schema import OperationLogRead
from ..operation_log.service import create_operation_log
from ..talent_profile_merge_log.model import TalentProfileMergeLog
from .const import TalentMergeStrategy
from .model import TalentProfile
from .schema import (
    TalentPendingMergeFieldRead,
    TalentPendingMergeRead,
    TalentProfileListItemRead,
    TalentProfileListPage,
    TalentProfileRead,
)

TALENT_FIELD_MAPPING: dict[str, str] = {
    CandidateFieldKey.FULL_NAME.value: "full_name",
    CandidateFieldKey.EMAIL.value: "email",
    CandidateFieldKey.WHATSAPP.value: "whatsapp",
    CandidateFieldKey.NATIONALITY.value: "nationality",
    CandidateFieldKey.COUNTRY_OF_RESIDENCE.value: "location",
    CandidateFieldKey.EDUCATION_STATUS.value: "education",
}

TALENT_ASSET_FIELD_MAPPING: dict[str, str] = {
    CandidateFieldKey.RESUME_ATTACHMENT.value: "resume_asset_id",
}


def _normalize_display_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        flattened = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(flattened) if flattened else None
    return str(value)


def _get_operation_log_title(log_type: str) -> str:
    if log_type == OperationLogType.CANDIDATE_APPLICATION_SUBMITTED.value:
        return "候选人提交报名"
    if log_type == OperationLogType.TALENT_PROFILE_INITIAL_AUTO_MERGE.value:
        return "首次自动创建人才快照"
    if log_type == OperationLogType.TALENT_PROFILE_LATEST_APPLICATION_UPDATED.value:
        return "更新最近申请岗位"
    if log_type == OperationLogType.TALENT_PROFILE_MANUAL_MERGE.value:
        return "手动合并人才快照"
    if log_type == OperationLogType.JOB_PROGRESS_CREATED.value:
        return "创建岗位流程记录"
    if log_type == OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value:
        return "岗位流程阶段变更"
    if log_type == OperationLogType.JOB_PROGRESS_ASSESSMENT_SUBMITTED.value:
        return "提交测试题附件"
    if log_type == OperationLogType.JOB_PROGRESS_CANDIDATE_SIGNED_CONTRACT_SUBMITTED.value:
        return "提交人选签回合同"
    if log_type == OperationLogType.JOB_PROGRESS_ASSESSMENT_REVIEW_UPDATED.value:
        return "更新测试题评审"
    if log_type == OperationLogType.JOB_PROGRESS_CONTRACT_DRAFT_UPLOADED.value:
        return "上传待签合同"
    if log_type == OperationLogType.JOB_PROGRESS_COMPANY_SEALED_CONTRACT_UPLOADED.value:
        return "上传公司盖章合同"
    return log_type


def _get_operation_log_actor_type(log_type: str) -> str:
    if log_type == OperationLogType.CANDIDATE_APPLICATION_SUBMITTED.value:
        return "candidate"
    if log_type == OperationLogType.JOB_PROGRESS_CANDIDATE_SIGNED_CONTRACT_SUBMITTED.value:
        return "candidate"
    if log_type == OperationLogType.TALENT_PROFILE_MANUAL_MERGE.value:
        return "admin"
    if log_type == OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value:
        return "system"
    if log_type in {
        OperationLogType.JOB_PROGRESS_ASSESSMENT_REVIEW_UPDATED.value,
        OperationLogType.JOB_PROGRESS_CONTRACT_DRAFT_UPLOADED.value,
        OperationLogType.JOB_PROGRESS_COMPANY_SEALED_CONTRACT_UPLOADED.value,
    }:
        return "admin"
    return "system"


def _get_operation_log_status_label(log: OperationLog) -> str | None:
    data = log.data or {}
    if log.log_type == OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value:
        value = data.get("to_stage_cn_name") or data.get("to_stage")
        return str(value) if value else None
    value = data.get("current_stage_cn_name") or data.get("current_stage")
    return str(value) if value else None


def _build_operation_log_summary(log: OperationLog, job_title: str | None) -> str:
    data = log.data or {}
    resolved_job_title = job_title or data.get("job_title") or "-"

    if log.log_type == OperationLogType.CANDIDATE_APPLICATION_SUBMITTED.value:
        count = data.get("submitted_items_count")
        return f"提交了 {resolved_job_title} 的报名表（字段数 {count or 0}）"
    if log.log_type == OperationLogType.TALENT_PROFILE_INITIAL_AUTO_MERGE.value:
        merged_fields = data.get("merged_fields") or []
        return f"系统根据申请 #{log.application_id or data.get('application_id') or '-'} 自动创建人才快照，合并了 {len(merged_fields)} 个字段"
    if log.log_type == OperationLogType.TALENT_PROFILE_LATEST_APPLICATION_UPDATED.value:
        return f"最近申请岗位更新为 {resolved_job_title}"
    if log.log_type == OperationLogType.TALENT_PROFILE_MANUAL_MERGE.value:
        merged_fields = data.get("merged_fields") or []
        return f"从申请 #{log.application_id or data.get('application_id') or '-'} 手动合并了 {len(merged_fields)} 个字段"
    if log.log_type == OperationLogType.JOB_PROGRESS_CREATED.value:
        stage_cn_name = data.get("current_stage_cn_name") or data.get("current_stage") or "-"
        return f"{resolved_job_title} 已创建流程记录，初始阶段为 {stage_cn_name}"
    if log.log_type == OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value:
        from_stage = data.get("from_stage_cn_name") or data.get("from_stage") or "-"
        to_stage = data.get("to_stage_cn_name") or data.get("to_stage") or "-"
        return f"{resolved_job_title} 从 {from_stage} 流转到 {to_stage}"
    if log.log_type == OperationLogType.JOB_PROGRESS_ASSESSMENT_SUBMITTED.value:
        attachment_name = data.get("assessment_attachment") or "-"
        return f"{resolved_job_title} 已提交测试题附件：{attachment_name}"
    if log.log_type == OperationLogType.JOB_PROGRESS_CANDIDATE_SIGNED_CONTRACT_SUBMITTED.value:
        attachment_name = data.get("submitted_contract_attachment") or "-"
        return f"{resolved_job_title} 已提交人选签回合同：{attachment_name}"
    if log.log_type == OperationLogType.JOB_PROGRESS_ASSESSMENT_REVIEW_UPDATED.value:
        updated_fields = data.get("updated_fields") or {}
        field_count = len(updated_fields) if isinstance(updated_fields, dict) else 0
        return f"{resolved_job_title} 已更新测试题评审信息（变更字段 {field_count} 项）"
    if log.log_type == OperationLogType.JOB_PROGRESS_CONTRACT_DRAFT_UPLOADED.value:
        attachment_name = data.get("contract_draft_attachment") or "-"
        return f"{resolved_job_title} 已上传待签合同：{attachment_name}"
    if log.log_type == OperationLogType.JOB_PROGRESS_COMPANY_SEALED_CONTRACT_UPLOADED.value:
        attachment_name = data.get("company_sealed_contract_attachment") or "-"
        return f"{resolved_job_title} 已上传公司盖章合同：{attachment_name}"
    return json.dumps(data, ensure_ascii=False) if data else "-"


def _serialize_raw_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _build_field_snapshot_map(job: Job) -> dict[str, dict[str, Any]]:
    data = job.data or {}
    return {
        str(field.get("key")): field
        for field in list(data.get(JOB_DATA_FORM_FIELDS_KEY) or [])
        if isinstance(field, dict) and field.get("key")
    }


def _merge_fields_into_profile(
    talent: TalentProfile,
    field_rows: Sequence[CandidateApplicationFieldValue],
    *,
    allowed_catalog_keys: set[str] | None = None,
) -> list[str]:
    merged_fields: list[str] = []
    for row in field_rows:
        catalog_key = row.catalog_key or row.field_key
        if allowed_catalog_keys is not None and catalog_key not in allowed_catalog_keys:
            continue

        display_value = row.display_value or row.raw_value
        if catalog_key in TALENT_FIELD_MAPPING and display_value is not None:
            setattr(talent, TALENT_FIELD_MAPPING[catalog_key], display_value)
            merged_fields.append(catalog_key)
        elif catalog_key in TALENT_ASSET_FIELD_MAPPING and row.asset_id is not None:
            setattr(talent, TALENT_ASSET_FIELD_MAPPING[catalog_key], row.asset_id)
            merged_fields.append(catalog_key)
    return merged_fields


async def _get_job_for_application(job_id: int, db: AsyncSession) -> Job:
    result = await db.execute(
        select(Job).where(
            Job.id == job_id,
            Job.is_deleted.is_(False),
            Job.status == JobStatus.OPEN.value,
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise NotFoundException("Job not found.")
    return job


async def _get_talent_profile_model(talent_id: int, db: AsyncSession) -> TalentProfile:
    result = await db.execute(
        select(TalentProfile).where(
            TalentProfile.id == talent_id,
            TalentProfile.is_deleted.is_(False),
        )
    )
    talent = result.scalar_one_or_none()
    if talent is None:
        raise NotFoundException("Talent profile not found.")
    return talent


async def _get_application_model(application_id: int, db: AsyncSession) -> CandidateApplication:
    result = await db.execute(
        select(CandidateApplication).where(
            CandidateApplication.id == application_id,
            CandidateApplication.is_deleted.is_(False),
        )
    )
    application = result.scalar_one_or_none()
    if application is None:
        raise NotFoundException("Application not found.")
    return application


async def _list_application_field_rows(application_id: int, db: AsyncSession) -> list[CandidateApplicationFieldValue]:
    result = await db.execute(
        select(CandidateApplicationFieldValue)
        .where(CandidateApplicationFieldValue.application_id == application_id)
        .order_by(CandidateApplicationFieldValue.sort_order.asc(), CandidateApplicationFieldValue.id.asc())
    )
    return list(result.scalars().all())


async def _serialize_talent_profile(talent: TalentProfile, db: AsyncSession) -> dict[str, Any]:
    asset_name: str | None = None
    if talent.resume_asset_id is not None:
        asset_result = await db.execute(
            select(Asset.original_name).where(
                Asset.id == talent.resume_asset_id,
                Asset.is_deleted.is_(False),
            )
        )
        asset_name = asset_result.scalar_one_or_none()

    applications_result = await db.execute(
        select(CandidateApplication)
        .where(
            CandidateApplication.user_id == talent.user_id,
            CandidateApplication.is_deleted.is_(False),
        )
        .order_by(CandidateApplication.submitted_at.desc(), CandidateApplication.id.desc())
        .limit(20)
    )
    application_models = list(applications_result.scalars().all())
    application_ids = [application.id for application in application_models]
    progress_map: dict[int, JobProgress] = {}
    if application_ids:
        progress_result = await db.execute(
            select(JobProgress)
            .where(
                JobProgress.application_id.in_(application_ids),
                JobProgress.is_deleted.is_(False),
            )
            .order_by(JobProgress.id.desc())
        )
        for progress in progress_result.scalars().all():
            progress_map.setdefault(int(progress.application_id), progress)

    applications = [
        CandidateApplicationSummaryRead(
            id=application.id,
            job_id=application.job_id,
            job_snapshot_title=application.job_snapshot_title,
            job_snapshot_company_name=application.job_snapshot_company_name,
            status=application.status,
            status_cn_name=get_candidate_application_status_cn_name(application.status),
            current_stage=progress_map.get(application.id).current_stage if progress_map.get(application.id) else None,
            current_stage_cn_name=(
                get_recruitment_stage_cn_name(progress_map.get(application.id).current_stage)
                if progress_map.get(application.id)
                else None
            ),
            submitted_at=application.submitted_at,
            source_of_current_snapshot=application.id == talent.source_application_id,
        ).model_dump()
        for application in application_models
    ]

    pending_merge = await _build_pending_merge_payload(
        talent=talent,
        db=db,
        applications=application_models,
        current_resume_asset_name=asset_name,
    )
    logs = await _list_talent_operation_logs(talent=talent, db=db)

    return TalentProfileRead(
        id=talent.id,
        user_id=talent.user_id,
        full_name=talent.full_name,
        email=talent.email,
        whatsapp=talent.whatsapp,
        nationality=talent.nationality,
        location=talent.location,
        education=talent.education,
        latest_applied_job_id=talent.latest_applied_job_id,
        latest_applied_job_title=talent.latest_applied_job_title,
        latest_applied_at=talent.latest_applied_at,
        resume_asset_id=talent.resume_asset_id,
        resume_asset_name=asset_name,
        note=talent.note,
        merge_strategy=talent.merge_strategy,
        source_application_id=talent.source_application_id,
        created_at=talent.created_at,
        last_merged_at=talent.last_merged_at,
        applications=applications,
        pending_merge=pending_merge,
        logs=logs,
    ).model_dump()


async def _list_talent_operation_logs(
    *,
    talent: TalentProfile,
    db: AsyncSession,
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(OperationLog)
        .where(
            or_(
                OperationLog.talent_profile_id == talent.id,
                and_(OperationLog.talent_profile_id.is_(None), OperationLog.user_id == talent.user_id),
            )
        )
        .order_by(OperationLog.created_at.desc(), OperationLog.id.desc())
    )
    log_models = list(result.scalars().all())

    application_ids = sorted({log.application_id for log in log_models if log.application_id is not None})
    job_ids = sorted({log.job_id for log in log_models if log.job_id is not None})

    application_titles: dict[int, str] = {}
    if application_ids:
        application_result = await db.execute(
            select(CandidateApplication.id, CandidateApplication.job_snapshot_title).where(
                CandidateApplication.id.in_(application_ids)
            )
        )
        application_titles = {int(application_id): job_snapshot_title for application_id, job_snapshot_title in application_result.all()}

    job_titles: dict[int, str] = {}
    if job_ids:
        job_result = await db.execute(select(Job.id, Job.title).where(Job.id.in_(job_ids)))
        job_titles = {int(job_id): title for job_id, title in job_result.all()}

    admin_user_ids = sorted(
        {
            int(operator_admin_user_id)
            for log in log_models
            for operator_admin_user_id in [(log.data or {}).get("operator_admin_user_id")]
            if operator_admin_user_id is not None
        }
    )
    admin_user_labels: dict[int, str] = {}
    if admin_user_ids:
        admin_user_result = await db.execute(
            select(AdminUser.id, AdminUser.name, AdminUser.username).where(AdminUser.id.in_(admin_user_ids))
        )
        admin_user_labels = {
            int(admin_user_id): (name or username or str(admin_user_id))
            for admin_user_id, name, username in admin_user_result.all()
        }

    items: list[dict[str, Any]] = []
    for log in log_models:
        job_title = None
        if log.application_id is not None:
            job_title = application_titles.get(log.application_id)
        if job_title is None and log.job_id is not None:
            job_title = job_titles.get(log.job_id)
        if job_title is None:
            raw_job_title = (log.data or {}).get("job_title")
            job_title = str(raw_job_title) if raw_job_title else None

        operator_admin_user_id = (log.data or {}).get("operator_admin_user_id")
        actor_name = None
        if operator_admin_user_id is not None:
            try:
                actor_name = admin_user_labels.get(int(operator_admin_user_id))
            except (TypeError, ValueError):
                actor_name = None

        items.append(
            OperationLogRead(
                id=log.id,
                user_id=log.user_id,
                job_id=log.job_id,
                job_title=job_title,
                application_id=log.application_id,
                talent_profile_id=log.talent_profile_id,
                log_type=log.log_type,
                title=_get_operation_log_title(log.log_type),
                summary=_build_operation_log_summary(log, job_title),
                actor_type=_get_operation_log_actor_type(log.log_type),
                actor_name=actor_name,
                status_label=_get_operation_log_status_label(log),
                data=log.data or {},
                created_at=log.created_at,
            ).model_dump()
        )
    return items


async def _build_pending_merge_payload(
    *,
    talent: TalentProfile,
    db: AsyncSession,
    applications: Sequence[CandidateApplication],
    current_resume_asset_name: str | None,
) -> dict[str, Any] | None:
    latest_application = applications[0] if applications else None
    if latest_application is None:
        return None
    if latest_application.id == talent.source_application_id:
        return None
    if talent.last_merged_at and latest_application.submitted_at <= talent.last_merged_at:
        return None

    field_rows = await _list_application_field_rows(latest_application.id, db)
    incoming_asset_ids = [
        row.asset_id
        for row in field_rows
        if row.asset_id is not None and (row.catalog_key or row.field_key) in TALENT_ASSET_FIELD_MAPPING
    ]
    incoming_asset_names: dict[int, str] = {}
    if incoming_asset_ids:
        asset_result = await db.execute(
            select(Asset.id, Asset.original_name).where(
                Asset.id.in_(incoming_asset_ids),
                Asset.is_deleted.is_(False),
            )
        )
        incoming_asset_names = {int(asset_id): original_name for asset_id, original_name in asset_result.all()}

    field_diffs: list[dict[str, Any]] = []
    for row in field_rows:
        catalog_key = row.catalog_key or row.field_key
        if catalog_key in TALENT_FIELD_MAPPING:
            current_value = getattr(talent, TALENT_FIELD_MAPPING[catalog_key], None)
            incoming_value = row.display_value or row.raw_value
        elif catalog_key in TALENT_ASSET_FIELD_MAPPING:
            current_value = current_resume_asset_name
            incoming_value = incoming_asset_names.get(row.asset_id or 0) or row.display_value or row.raw_value
        else:
            continue

        normalized_current = (current_value or "").strip()
        normalized_incoming = (incoming_value or "").strip()
        if normalized_current == normalized_incoming:
            continue

        field_diffs.append(
            TalentPendingMergeFieldRead(
                key=catalog_key,
                label=row.field_label,
                current_value=current_value,
                incoming_value=incoming_value,
            ).model_dump()
        )

    if not field_diffs:
        return None

    return TalentPendingMergeRead(
        application_id=latest_application.id,
        submitted_at=latest_application.submitted_at,
        fields=field_diffs,
    ).model_dump()


async def create_application_and_sync_talent(
    *,
    job_id: int,
    payload: CandidateApplicationSubmitRequest,
    current_user: dict[str, Any],
    db: AsyncSession,
) -> dict[str, Any]:
    job = await _get_job_for_application(job_id, db)
    existing_application_result = await db.execute(
        select(CandidateApplication.id).where(
            CandidateApplication.user_id == current_user["id"],
            CandidateApplication.job_id == job.id,
            CandidateApplication.is_deleted.is_(False),
        )
    )
    if existing_application_result.scalar_one_or_none() is not None:
        raise BadRequestException("You have already applied to this role.")
    field_snapshot_map = _build_field_snapshot_map(job)

    application = CandidateApplication(
        user_id=current_user["id"],
        job_id=job.id,
        form_template_id=job.form_template_id,
        job_snapshot_title=job.title,
        job_snapshot_company_name=job.company_name,
        status="submitted",
        submitted_at=datetime.now(UTC),
        data={"submitted_items_count": len(payload.items)},
    )
    db.add(application)
    await db.flush()

    next_order = 0
    for item in payload.items:
        snapshot = field_snapshot_map.get(item.field_key, {})
        display_value = item.display_value or _normalize_display_value(item.value)
        row = CandidateApplicationFieldValue(
            application_id=application.id,
            field_key=item.field_key,
            field_label=str(snapshot.get("label") or item.field_key),
            field_type=str(snapshot.get("type") or "text"),
            catalog_key=item.field_key,
            raw_value=_serialize_raw_value(item.value),
            display_value=display_value,
            asset_id=item.asset_id,
            sort_order=next_order,
        )
        db.add(row)
        next_order += 1

    await db.flush()

    submitted_log = await create_operation_log(
        db=db,
        user_id=current_user["id"],
        job_id=job.id,
        application_id=application.id,
        log_type=OperationLogType.CANDIDATE_APPLICATION_SUBMITTED.value,
        data={
            "application_id": application.id,
            "job_id": job.id,
            "job_title": job.title,
            "submitted_items_count": len(payload.items),
        },
    )

    talent_result = await db.execute(
        select(TalentProfile).where(
            TalentProfile.user_id == current_user["id"],
            TalentProfile.is_deleted.is_(False),
        )
    )
    talent = talent_result.scalar_one_or_none()

    auto_merged = False
    talent_created = False
    if talent is None:
        talent = TalentProfile(
            user_id=current_user["id"],
            full_name=current_user.get("name"),
            email=current_user.get("email"),
            latest_applied_job_id=job.id,
            latest_applied_job_title=job.title,
            latest_applied_at=application.submitted_at,
            source_application_id=application.id,
            merge_strategy=TalentMergeStrategy.INITIAL_AUTO.value,
            last_merged_at=application.submitted_at,
            data={},
        )
        db.add(talent)
        await db.flush()

        field_rows = await _list_application_field_rows(application.id, db)
        merged_fields = _merge_fields_into_profile(talent, field_rows)
        if CandidateFieldKey.EMAIL.value not in merged_fields and current_user.get("email"):
            talent.email = current_user["email"]
        if CandidateFieldKey.FULL_NAME.value not in merged_fields and current_user.get("name"):
            talent.full_name = current_user["name"]

        db.add(
            TalentProfileMergeLog(
                talent_profile_id=talent.id,
                application_id=application.id,
                operator_admin_user_id=None,
                merge_strategy=TalentMergeStrategy.INITIAL_AUTO.value,
                merged_fields=merged_fields,
            )
        )
        await create_operation_log(
            db=db,
            user_id=current_user["id"],
            job_id=job.id,
            application_id=application.id,
            talent_profile_id=talent.id,
            log_type=OperationLogType.TALENT_PROFILE_INITIAL_AUTO_MERGE.value,
            data={
                "talent_profile_id": talent.id,
                "application_id": application.id,
                "job_id": job.id,
                "job_title": job.title,
                "merged_fields": merged_fields,
            },
        )
        auto_merged = True
        talent_created = True
    else:
        talent.latest_applied_job_id = job.id
        talent.latest_applied_job_title = job.title
        talent.latest_applied_at = application.submitted_at
        await create_operation_log(
            db=db,
            user_id=current_user["id"],
            job_id=job.id,
            application_id=application.id,
            talent_profile_id=talent.id,
            log_type=OperationLogType.TALENT_PROFILE_LATEST_APPLICATION_UPDATED.value,
            data={
                "talent_profile_id": talent.id,
                "application_id": application.id,
                "latest_applied_job_id": job.id,
                "job_title": job.title,
            },
        )

    submitted_log.talent_profile_id = talent.id

    field_rows = await _list_application_field_rows(application.id, db)
    await create_job_progress_for_application(
        job=job,
        application=application,
        talent_profile_id=talent.id,
        field_rows=field_rows,
        db=db,
    )

    await db.commit()

    return CandidateApplicationSubmitResponse(
        application_id=application.id,
        talent_profile_id=talent.id,
        talent_profile_created=talent_created,
        auto_merged=auto_merged,
    ).model_dump()


async def merge_application_into_talent(
    *,
    talent_id: int,
    application_id: int,
    current_admin: dict[str, Any],
    db: AsyncSession,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    talent = await _get_talent_profile_model(talent_id, db)
    application = await _get_application_model(application_id, db)
    if application.user_id != talent.user_id:
        raise BadRequestException("Application does not belong to this talent profile.")

    field_rows = await _list_application_field_rows(application.id, db)
    merged_fields = _merge_fields_into_profile(
        talent,
        field_rows,
        allowed_catalog_keys=set(fields) if fields else None,
    )
    if not merged_fields:
        raise BadRequestException("No eligible fields were found to merge.")

    talent.source_application_id = application.id
    talent.merge_strategy = TalentMergeStrategy.MANUAL.value
    talent.last_merged_at = datetime.now(UTC)
    talent.latest_applied_job_id = application.job_id
    talent.latest_applied_job_title = application.job_snapshot_title
    talent.latest_applied_at = application.submitted_at

    db.add(
        TalentProfileMergeLog(
            talent_profile_id=talent.id,
            application_id=application.id,
            operator_admin_user_id=current_admin["id"],
            merge_strategy=TalentMergeStrategy.MANUAL.value,
            merged_fields=merged_fields,
        )
    )
    await create_operation_log(
        db=db,
        user_id=application.user_id,
        job_id=application.job_id,
        application_id=application.id,
        talent_profile_id=talent.id,
        log_type=OperationLogType.TALENT_PROFILE_MANUAL_MERGE.value,
        data={
            "talent_profile_id": talent.id,
            "application_id": application.id,
            "job_id": application.job_id,
            "job_title": application.job_snapshot_title,
            "operator_admin_user_id": current_admin["id"],
            "merged_fields": merged_fields,
        },
    )
    await db.commit()
    await db.refresh(talent)
    return await _serialize_talent_profile(talent, db)


async def get_talent_profile(talent_id: int, db: AsyncSession) -> dict[str, Any]:
    talent = await _get_talent_profile_model(talent_id, db)
    return await _serialize_talent_profile(talent, db)


async def list_talent_profiles(
    db: AsyncSession,
    *,
    page: int,
    page_size: int,
    keyword: str | None = None,
) -> dict[str, Any]:
    conditions = [TalentProfile.is_deleted.is_(False)]
    if keyword:
        term = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                TalentProfile.full_name.ilike(term),
                TalentProfile.email.ilike(term),
                TalentProfile.whatsapp.ilike(term),
                TalentProfile.nationality.ilike(term),
                TalentProfile.location.ilike(term),
                TalentProfile.education.ilike(term),
                TalentProfile.latest_applied_job_title.ilike(term),
                TalentProfile.note.ilike(term),
            )
        )

    total_result = await db.execute(select(func.count()).select_from(TalentProfile).where(*conditions))
    total = int(total_result.scalar() or 0)

    result = await db.execute(
        select(TalentProfile, Asset.original_name)
        .outerjoin(Asset, Asset.id == TalentProfile.resume_asset_id)
        .where(*conditions)
        .order_by(
            TalentProfile.latest_applied_at.is_(None).asc(),
            TalentProfile.latest_applied_at.desc(),
            TalentProfile.created_at.desc(),
            TalentProfile.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )

    items = [
        TalentProfileListItemRead(
            id=talent.id,
            user_id=talent.user_id,
            full_name=talent.full_name,
            email=talent.email,
            whatsapp=talent.whatsapp,
            nationality=talent.nationality,
            location=talent.location,
            education=talent.education,
            latest_applied_job_title=talent.latest_applied_job_title,
            resume_asset_id=talent.resume_asset_id,
            resume_asset_name=asset_name,
            note=talent.note,
            latest_applied_at=talent.latest_applied_at,
            created_at=talent.created_at,
            merge_strategy=talent.merge_strategy,
            source_application_id=talent.source_application_id,
        ).model_dump()
        for talent, asset_name in result.all()
    ]

    return TalentProfileListPage(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    ).model_dump()
