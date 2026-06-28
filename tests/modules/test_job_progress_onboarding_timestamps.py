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
    onboarding_status: str | None = None,
    salary_confirmed_at: str | None = None,
    gift_package_sent_at: str | None = None,
    update_salary_confirmed_at: bool = False,
    update_gift_package_sent_at: bool = False,
) -> dict[str, Any]:
    operation_logs: list[dict[str, Any]] = []

    async def fake_get_job_progress_models(**_kwargs: Any) -> list[SimpleNamespace]:
        return [progress]

    async def fake_create_operation_log(**kwargs: Any) -> None:
        operation_logs.append(kwargs)

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
        salary_confirmed_at=salary_confirmed_at,
        gift_package_sent_at=gift_package_sent_at,
        update_salary_confirmed_at=update_salary_confirmed_at,
        update_gift_package_sent_at=update_gift_package_sent_at,
    )
    assert db.flushed is True
    response["operation_logs"] = operation_logs
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


async def test_bargain_sent_status_sets_salary_confirmed_date(monkeypatch: pytest.MonkeyPatch) -> None:
    progress = _progress()

    response = await _run_onboarding_update(monkeypatch, progress=progress, onboarding_status="已发砍价")

    assert progress.data[JobProgressDataKey.ONBOARDING_STATUS.value] == "已发砍价"
    assert progress.data[JobProgressDataKey.SALARY_CONFIRMED_AT.value] == "2026-06-24"
    assert response["updated_field_keys"] == ["onboarding_status", "salary_confirmed_at"]


async def test_bargain_sent_status_keeps_existing_salary_confirmed_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress = _progress(
        data={
            JobProgressDataKey.ONBOARDING_STATUS.value: "砍价中",
            JobProgressDataKey.SALARY_CONFIRMED_AT.value: "2026-06-23",
        }
    )

    response = await _run_onboarding_update(monkeypatch, progress=progress, onboarding_status="已发砍价")

    assert progress.data[JobProgressDataKey.ONBOARDING_STATUS.value] == "已发砍价"
    assert progress.data[JobProgressDataKey.SALARY_CONFIRMED_AT.value] == "2026-06-23"
    assert response["updated_field_keys"] == ["onboarding_status"]


async def test_salary_confirmed_date_can_be_manually_corrected_with_operation_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress = _progress(
        data={
            JobProgressDataKey.ONBOARDING_STATUS.value: "已发砍价",
            JobProgressDataKey.SALARY_CONFIRMED_AT.value: "2026-06-24",
        }
    )

    response = await _run_onboarding_update(
        monkeypatch,
        progress=progress,
        salary_confirmed_at="2026-06-25",
        update_salary_confirmed_at=True,
    )

    assert progress.data[JobProgressDataKey.SALARY_CONFIRMED_AT.value] == "2026-06-25"
    assert response["updated_field_keys"] == ["salary_confirmed_at"]
    assert response["operation_logs"][0]["data"]["updated_fields"] == {
        "salary_confirmed_at": {
            "from": "2026-06-24",
            "to": "2026-06-25",
        },
    }


async def test_gift_package_sent_date_can_be_manually_corrected_with_operation_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress = _progress(
        data={
            JobProgressDataKey.ONBOARDING_STATUS.value: "已发大礼包",
            JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value: "2026-06-24 14:35:20",
        }
    )

    response = await _run_onboarding_update(
        monkeypatch,
        progress=progress,
        gift_package_sent_at="2026-06-25",
        update_gift_package_sent_at=True,
    )

    assert progress.data[JobProgressDataKey.GIFT_PACKAGE_SENT_AT.value] == "2026-06-25"
    assert response["updated_field_keys"] == ["gift_package_sent_at"]
    assert response["operation_logs"][0]["data"]["updated_fields"] == {
        "gift_package_sent_at": {
            "from": "2026-06-24 14:35:20",
            "to": "2026-06-25",
        },
    }
