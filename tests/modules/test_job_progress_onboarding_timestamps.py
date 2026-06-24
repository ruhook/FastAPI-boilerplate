from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.modules.job_progress import service as job_progress_service
from src.app.modules.job_progress.const import JobProgressDataKey, RecruitmentStage

pytestmark = [pytest.mark.asyncio, pytest.mark.no_database_cleanup]


class _FakeScalarResult:
    def __init__(self, item: Any) -> None:
        self.item = item

    def scalar_one_or_none(self) -> Any:
        return self.item


class _FakeDb:
    def __init__(self, job: Any) -> None:
        self.job = job
        self.flushed = False

    async def execute(self, _statement: Any) -> _FakeScalarResult:
        return _FakeScalarResult(self.job)

    async def flush(self) -> None:
        self.flushed = True


class _FixedDateTime:
    @classmethod
    def now(cls, _tz: Any = None) -> datetime:
        return datetime(2026, 6, 24, 14, 35, 20)


def _progress(*, data: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=101,
        job_id=12,
        user_id=23,
        application_id=34,
        talent_profile_id=None,
        current_stage=RecruitmentStage.ACTIVE.value,
        data=data or {},
    )


async def _run_onboarding_update(
    monkeypatch: pytest.MonkeyPatch,
    *,
    progress: SimpleNamespace,
    onboarding_status: str,
) -> dict[str, Any]:
    async def fake_get_job_progress_models(**_kwargs: Any) -> list[SimpleNamespace]:
        return [progress]

    async def fake_create_operation_log(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(job_progress_service, "datetime", _FixedDateTime)
    monkeypatch.setattr(job_progress_service, "get_job_progress_models", fake_get_job_progress_models)
    monkeypatch.setattr(job_progress_service, "create_operation_log", fake_create_operation_log)

    db = _FakeDb(SimpleNamespace(id=12, title="Timestamp Probe"))
    response = await job_progress_service.update_job_progress_onboarding(
        job_id=12,
        progress_ids=[101],
        admin_user_id=1,
        db=db,
        onboarding_status=onboarding_status,
    )
    assert db.flushed is True
    return response


async def test_joined_group_status_sets_backend_onboarding_datetime(monkeypatch: pytest.MonkeyPatch) -> None:
    progress = _progress()

    response = await _run_onboarding_update(monkeypatch, progress=progress, onboarding_status="已进群")

    assert progress.data[JobProgressDataKey.ONBOARDING_STATUS.value] == "已进群"
    assert progress.data[JobProgressDataKey.ONBOARDING_DATE.value] == "2026-06-24 14:35:20"
    assert response["updated_field_keys"] == ["onboarding_date", "onboarding_status"]


async def test_gift_package_status_sets_backend_sent_datetime(monkeypatch: pytest.MonkeyPatch) -> None:
    progress = _progress()

    response = await _run_onboarding_update(monkeypatch, progress=progress, onboarding_status="已发大礼包")

    assert progress.data[JobProgressDataKey.ONBOARDING_STATUS.value] == "已发大礼包"
    assert progress.data[JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value] == "2026-06-24 14:35:20"
    assert response["updated_field_keys"] == ["gift_package_sent_at", "onboarding_status"]
