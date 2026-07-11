from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.app.core.exceptions.http_exceptions import BadRequestException
from src.app.modules.admin.mail_reference_policy import ensure_mail_resource_not_used_by_job
from src.app.modules.job.const import JOB_DATA_REJECTION_MAIL_CONFIG_KEY

pytestmark = [pytest.mark.asyncio(loop_scope="session"), pytest.mark.no_database_cleanup]


@pytest.mark.parametrize(
    ("resource_type", "assessment_attribute", "rejection_key"),
    [
        ("account", "assessment_mail_account_id", "mail_account_id"),
        ("template", "assessment_mail_template_id", "mail_template_id"),
        ("signature", "assessment_mail_signature_id", "mail_signature_id"),
    ],
)
async def test_referenced_mail_resources_cannot_be_deleted(
    resource_type: str,
    assessment_attribute: str,
    rejection_key: str,
) -> None:
    assessment_job = SimpleNamespace(
        id=10,
        title="Assessment Job",
        assessment_mail_account_id=None,
        assessment_mail_template_id=None,
        assessment_mail_signature_id=None,
        data={},
    )
    setattr(assessment_job, assessment_attribute, 42)
    rejection_job = SimpleNamespace(
        id=11,
        title="Rejection Job",
        assessment_mail_account_id=None,
        assessment_mail_template_id=None,
        assessment_mail_signature_id=None,
        data={JOB_DATA_REJECTION_MAIL_CONFIG_KEY: {rejection_key: 42}},
    )

    for job in (assessment_job, rejection_job):
        scalar_result = MagicMock()
        scalar_result.all.return_value = [job]
        db = AsyncMock()
        db.scalars.return_value = scalar_result
        with pytest.raises(BadRequestException, match="still used by job"):
            await ensure_mail_resource_not_used_by_job(
                db=db,
                resource_type=resource_type,
                resource_id=42,
            )
