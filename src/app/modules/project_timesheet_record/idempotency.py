import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import JSON, ForeignKey, String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from ...core.db.database import Base
from ...core.db.models import StandardEntityMixin
from ...core.exceptions.http_exceptions import ConflictException
from .schema import ProjectTimesheetBatchCreateRequest


class ProjectTimesheetBatchRequest(StandardEntityMixin, Base):
    __tablename__ = "project_timesheet_batch_request"

    idempotency_key: Mapped[str] = mapped_column(String(191), nullable=False, unique=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    company_id: Mapped[int] = mapped_column(ForeignKey("admin_company.id"), nullable=False, index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("admin_company_project.id"), nullable=False, index=True)
    admin_user_id: Mapped[int] = mapped_column(ForeignKey("admin_user.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="processing")
    record_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    request: ProjectTimesheetBatchRequest
    replayed_record_ids: list[int] | None


def build_timesheet_request_hash(
    *,
    company_id: int,
    project_id: int,
    admin_user_id: int,
    payload: ProjectTimesheetBatchCreateRequest,
) -> str:
    encoded = json.dumps(
        {
            "admin_user_id": admin_user_id,
            "company_id": company_id,
            "payload": payload.model_dump(mode="json", exclude={"idempotency_key"}),
            "project_id": project_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _validate_existing_request(
    request: ProjectTimesheetBatchRequest,
    *,
    request_hash: str,
) -> IdempotencyClaim:
    if request.request_hash != request_hash:
        raise ConflictException("Idempotency key was already used with a different timesheet request.")
    if request.status != "completed":
        raise ConflictException("Timesheet request is still being processed.")
    return IdempotencyClaim(request=request, replayed_record_ids=list(request.record_ids))


async def claim_timesheet_request(
    *,
    db: AsyncSession,
    company_id: int,
    project_id: int,
    admin_user_id: int,
    payload: ProjectTimesheetBatchCreateRequest,
) -> IdempotencyClaim:
    request_hash = build_timesheet_request_hash(
        company_id=company_id,
        project_id=project_id,
        admin_user_id=admin_user_id,
        payload=payload,
    )
    existing = (
        await db.scalars(
            select(ProjectTimesheetBatchRequest)
            .where(ProjectTimesheetBatchRequest.idempotency_key == payload.idempotency_key)
            .with_for_update()
        )
    ).one_or_none()
    if existing is not None:
        return _validate_existing_request(existing, request_hash=request_hash)

    request = ProjectTimesheetBatchRequest(
        idempotency_key=payload.idempotency_key,
        request_hash=request_hash,
        company_id=company_id,
        project_id=project_id,
        admin_user_id=admin_user_id,
        status="processing",
        record_ids=[],
    )
    try:
        async with db.begin_nested():
            db.add(request)
            await db.flush()
    except IntegrityError:
        existing = (
            await db.scalars(
                select(ProjectTimesheetBatchRequest)
                .where(ProjectTimesheetBatchRequest.idempotency_key == payload.idempotency_key)
                .with_for_update()
            )
        ).one_or_none()
        if existing is None:
            raise
        return _validate_existing_request(existing, request_hash=request_hash)
    return IdempotencyClaim(request=request, replayed_record_ids=None)


async def complete_timesheet_request(
    *,
    db: AsyncSession,
    request: ProjectTimesheetBatchRequest,
    record_ids: list[int],
) -> None:
    request.status = "completed"
    request.record_ids = list(record_ids)
    await db.flush()
