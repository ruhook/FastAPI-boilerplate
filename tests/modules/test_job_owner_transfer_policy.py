from types import SimpleNamespace

import pytest

from src.app.core.exceptions.http_exceptions import BadRequestException
from src.app.modules.job.commands import _ensure_owner_transfer_mail_config
from src.app.modules.job.const import JOB_DATA_REJECTION_MAIL_CONFIG_KEY
from src.app.modules.job.schema import JobAssessmentConfig, JobRejectionMailConfig, JobUpdate

pytestmark = pytest.mark.no_database_cleanup


def test_owner_transfer_requires_explicit_enabled_mail_config_migration() -> None:
    job = SimpleNamespace(
        assessment_enabled=True,
        data={JOB_DATA_REJECTION_MAIL_CONFIG_KEY: {"enabled": True}},
    )

    with pytest.raises(BadRequestException, match="assessment mail configuration"):
        _ensure_owner_transfer_mail_config(
            owner_is_changing=True,
            job=job,
            payload=JobUpdate(owner_admin_user_id=2),
        )


def test_owner_transfer_accepts_explicit_disable_for_both_mail_workflows() -> None:
    job = SimpleNamespace(
        assessment_enabled=True,
        data={JOB_DATA_REJECTION_MAIL_CONFIG_KEY: {"enabled": True}},
    )

    _ensure_owner_transfer_mail_config(
        owner_is_changing=True,
        job=job,
        payload=JobUpdate(
            owner_admin_user_id=2,
            assessment_config=JobAssessmentConfig(enabled=False),
            rejection_mail_config=JobRejectionMailConfig(enabled=False),
        ),
    )
