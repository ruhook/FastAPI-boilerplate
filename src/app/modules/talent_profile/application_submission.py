from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import BadRequestException, NotFoundException
from ..assets.service import ensure_assets_belong_to_owner
from ..candidate_application.model import CandidateApplication
from ..candidate_application.schema import (
    CandidateApplicationSubmitRequest,
    CandidateApplicationSubmitResponse,
)
from ..candidate_application_field_value.model import CandidateApplicationFieldValue
from ..candidate_field.const import CandidateFieldKey
from ..candidate_field.service import hydrate_candidate_field_options
from ..job.const import JOB_DATA_APPLICATION_SUMMARY_KEY, JOB_DATA_FORM_FIELDS_KEY, JobStatus
from ..job.model import Job
from ..job_progress.service import create_job_progress_for_application
from ..operation_log.const import OperationLogType
from ..operation_log.service import create_operation_log
from ..talent_profile_merge_log.model import TalentProfileMergeLog
from .const import TalentMergeStrategy
from .merge import _merge_fields_into_profile
from .model import TalentProfile
from .queries import _list_application_field_rows
from .serialization import (
    _is_blank_application_value,
    _normalize_display_value,
    _normalize_option_values,
    _normalize_submitted_option_values,
    _serialize_raw_value,
)


async def _validate_application_items(
    *,
    job: Job,
    payload: CandidateApplicationSubmitRequest,
    current_user: dict[str, Any],
    db: AsyncSession,
) -> tuple[dict[str, dict[str, Any]], list[Any]]:
    raw_fields = [
        dict(field)
        for field in list((job.data or {}).get(JOB_DATA_FORM_FIELDS_KEY) or [])
        if isinstance(field, dict) and field.get("key")
    ]
    hydrated_fields = [
        field
        for field in await hydrate_candidate_field_options(raw_fields, db=db)
        if field.get("visible", True) is not False
    ]
    field_snapshot_map = {
        str(field.get("key")): dict(field) for field in hydrated_fields if isinstance(field, dict) and field.get("key")
    }
    submitted_keys: set[str] = set()
    asset_ids: list[int] = []

    for item in payload.items:
        field_key = str(item.field_key)
        if field_key == CandidateFieldKey.FULL_NAME.value:
            account_name = str(current_user.get("name") or current_user.get("email") or "").strip()
            item.value = account_name
            item.display_value = account_name
        elif field_key == CandidateFieldKey.EMAIL.value:
            account_email = str(current_user.get("email") or "").strip()
            item.value = account_email
            item.display_value = account_email
        if field_key in submitted_keys:
            raise BadRequestException(f"Duplicate application field: {field_key}.")
        submitted_keys.add(field_key)
        snapshot = field_snapshot_map.get(field_key)
        if snapshot is None:
            raise BadRequestException(f"Unsupported application field: {field_key}.")

        field_type = str(snapshot.get("type") or "text").strip().lower()
        is_file_field = field_type == "file"
        is_blank = _is_blank_application_value(item.value, item.display_value)

        if bool(snapshot.get("required")):
            if is_file_field:
                if item.asset_id in (None, 0, ""):
                    raise BadRequestException(f"{snapshot.get('label') or field_key} is required.")
            elif is_blank:
                raise BadRequestException(f"{snapshot.get('label') or field_key} is required.")

        if item.asset_id not in (None, 0, ""):
            if not is_file_field:
                raise BadRequestException(f"{snapshot.get('label') or field_key} does not accept attachments.")
            if item.asset_id is not None:
                asset_ids.append(int(item.asset_id))

        if field_type in {"select", "dictionary", "multiselect"} and not is_blank:
            allowed_values = _normalize_option_values(snapshot.get("options"))
            if allowed_values:
                submitted_values = _normalize_submitted_option_values(item.value, item.display_value)
                unsupported_values = [value for value in submitted_values if value not in allowed_values]
                if unsupported_values:
                    raise BadRequestException(f"Invalid option for {snapshot.get('label') or field_key}.")

        if field_type == "number" and not is_blank:
            try:
                float(str(item.value).strip())
            except (TypeError, ValueError):
                raise BadRequestException(f"{snapshot.get('label') or field_key} must be a number.")

    missing_required_fields = [
        field
        for field in hydrated_fields
        if bool(field.get("required")) and str(field.get("key")) not in submitted_keys
    ]
    if missing_required_fields:
        first_missing = missing_required_fields[0]
        raise BadRequestException(f"{first_missing.get('label') or first_missing.get('key')} is required.")

    if asset_ids:
        assets = await ensure_assets_belong_to_owner(
            db,
            owner_type="user",
            owner_id=int(current_user["id"]),
            asset_ids=asset_ids,
        )
        invalid_assets = [asset.id for asset in assets if asset.module != "candidate_application" or asset.is_deleted]
        if invalid_assets:
            raise BadRequestException("Invalid application attachment.")

    return field_snapshot_map, list(payload.items)


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
    field_snapshot_map, validated_items = await _validate_application_items(
        job=job,
        payload=payload,
        current_user=current_user,
        db=db,
    )

    next_count = Job.applicant_count + 1
    summary_object_path = f"$.{JOB_DATA_APPLICATION_SUMMARY_KEY}"
    summary_applicants_path = f"{summary_object_path}.applicants"
    next_job_data = case(
        (
            func.json_type(func.json_extract(Job.data, summary_object_path)) == "OBJECT",
            func.json_set(Job.data, summary_applicants_path, next_count),
        ),
        else_=Job.data,
    )
    await db.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(applicant_count=next_count, data=next_job_data)
        .execution_options(synchronize_session=False)
    )
    await db.refresh(job, attribute_names=["applicant_count", "data"])

    application = CandidateApplication(
        user_id=current_user["id"],
        job_id=job.id,
        form_template_id=job.form_template_id,
        job_snapshot_title=job.title,
        status="submitted",
        submitted_at=datetime.now(UTC),
        data={"submitted_items_count": len(validated_items)},
    )
    db.add(application)
    try:
        await db.flush()
    except IntegrityError as exc:
        if "uq_candidate_application_active_user_job" in str(exc.orig):
            raise BadRequestException("You have already applied to this role.") from None
        raise

    next_order = 0
    for item in validated_items:
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
            "submitted_items_count": len(validated_items),
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

    await db.flush()

    return CandidateApplicationSubmitResponse(
        application_id=application.id,
        talent_profile_id=talent.id,
        talent_profile_created=talent_created,
        auto_merged=auto_merged,
    ).model_dump()
