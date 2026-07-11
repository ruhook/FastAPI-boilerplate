from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import BadRequestException
from ..candidate_application_field_value.model import CandidateApplicationFieldValue
from ..operation_log.const import OperationLogType
from ..operation_log.service import create_operation_log
from ..talent_profile_merge_log.model import TalentProfileMergeLog
from .const import TalentMergeStrategy
from .model import TalentProfile
from .queries import (
    _get_application_model,
    _get_talent_profile_model,
    _list_application_field_rows,
    _serialize_talent_profile,
)
from .serialization import TALENT_ASSET_FIELD_MAPPING, TALENT_FIELD_MAPPING


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
    await db.flush()
    await db.refresh(talent)
    return await _serialize_talent_profile(talent, db)

