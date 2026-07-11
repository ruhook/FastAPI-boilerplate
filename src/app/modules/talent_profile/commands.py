from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import BadRequestException
from ..job_progress.const import JobProgressDataKey, RecruitmentStage
from .pool_fields import (
    TALENT_STATUS_REPLACED,
    load_talent_pool_sources,
    validate_manual_talent_status,
)
from .queries import _get_talent_profile_model, _serialize_talent_profile


async def update_talent_pool_note(
    *,
    talent_id: int,
    note: str | None,
    current_admin: dict[str, Any],
    db: AsyncSession,
) -> dict[str, Any]:
    talent = await _get_talent_profile_model(talent_id, db)
    source_bundle = await load_talent_pool_sources(db=db, talents=[talent])
    progress = source_bundle.progress_by_talent.get(int(talent.id))
    normalized_note = (note or "").strip() or None
    if progress is not None:
        progress.data = {
            **(progress.data or {}),
            JobProgressDataKey.NOTE.value: normalized_note,
        }
    talent.note = normalized_note
    await db.flush()
    await db.refresh(talent)
    return await _serialize_talent_profile(talent, db)


async def update_talent_pool_status(
    *,
    talent_id: int,
    status: str,
    current_admin: dict[str, Any],
    db: AsyncSession,
) -> dict[str, Any]:
    talent = await _get_talent_profile_model(talent_id, db)
    source_bundle = await load_talent_pool_sources(db=db, talents=[talent])
    progress = source_bundle.progress_by_talent.get(int(talent.id))
    try:
        normalized_status = validate_manual_talent_status(status, progress)
    except ValueError as exc:
        raise BadRequestException(str(exc)) from exc
    talent.status_override = normalized_status
    if normalized_status == TALENT_STATUS_REPLACED and progress is not None:
        progress.current_stage = RecruitmentStage.REPLACED.value
    await db.flush()
    await db.refresh(talent)
    return await _serialize_talent_profile(talent, db)

