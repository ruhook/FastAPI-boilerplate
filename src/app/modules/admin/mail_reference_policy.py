from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import BadRequestException
from ..job.const import JOB_DATA_REJECTION_MAIL_CONFIG_KEY
from ..job.model import Job

MailResourceType = Literal["account", "template", "signature"]


async def ensure_mail_resource_not_used_by_job(
    *,
    db: AsyncSession,
    resource_type: MailResourceType,
    resource_id: int,
) -> None:
    jobs = list(
        (
            await db.scalars(
                select(Job).where(Job.is_deleted.is_(False)).order_by(Job.id.asc())
            )
        ).all()
    )
    assessment_attribute = f"assessment_mail_{resource_type}_id"
    rejection_key = f"mail_{resource_type}_id"
    for job in jobs:
        assessment_reference = getattr(job, assessment_attribute, None)
        raw_rejection = (job.data or {}).get(JOB_DATA_REJECTION_MAIL_CONFIG_KEY) or {}
        rejection_reference = raw_rejection.get(rejection_key) if isinstance(raw_rejection, dict) else None
        if str(assessment_reference or "") == str(resource_id) or str(rejection_reference or "") == str(resource_id):
            raise BadRequestException(
                f"Mail {resource_type} is still used by job '{job.title}' (ID {job.id}). "
                "Update the job mail configuration first."
            )
