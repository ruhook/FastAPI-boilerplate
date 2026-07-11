from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from src.app.modules.job_progress import commands as job_progress_commands
from src.app.modules.job_progress import contract_workflow as job_progress_contract_workflow
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


def _job() -> SimpleNamespace:
    return SimpleNamespace(id=12, title="Clear Editable Fields")


def _progress(*, data: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=101,
        job_id=12,
        user_id=23,
        application_id=34,
        talent_profile_id=None,
        current_stage=RecruitmentStage.SCREENING_PASSED.value,
        data=data or {},
    )


def _contract_record(
    *,
    progress: SimpleNamespace,
    agreement_ref_no: str | None = "CN-001",
    rate: Decimal | None = Decimal("2.00"),
) -> SimpleNamespace:
    return SimpleNamespace(
        id=56,
        user_id=progress.user_id,
        talent_profile_id=progress.talent_profile_id,
        application_id=progress.application_id,
        job_id=progress.job_id,
        job_progress_id=progress.id,
        service_customer_company_id=None,
        service_customer_project_id=None,
        agreement_ref_no=agreement_ref_no,
        contract_status="Pending Activation",
        contract_type=None,
        contractor_name="Clear Candidate",
        rate=rate,
        base_pay=None,
        legal_entity="T-Maxx International",
        worker_type="Contractor",
        effective_date=None,
        end_date=None,
        draft_contract_asset_id=None,
        candidate_signed_contract_asset_id=None,
        company_sealed_contract_asset_id=None,
        contract_attachment_asset_id=None,
        parse_status="pending",
        parse_error=None,
        data={},
    )


async def _noop_operation_log(**_kwargs: Any) -> None:
    return None


async def test_contract_number_can_be_cleared_with_explicit_null(monkeypatch: pytest.MonkeyPatch) -> None:
    progress = _progress()
    captured_field_updates: dict[str, Any] = {}

    async def fake_get_job_progress_models(**_kwargs: Any) -> list[SimpleNamespace]:
        return [progress]

    async def fake_upsert_contract_record_for_progress(**kwargs: Any) -> SimpleNamespace:
        captured_field_updates.update(kwargs["field_updates"])
        return _contract_record(
            progress=kwargs["progress"],
            agreement_ref_no=kwargs["field_updates"].get("agreement_ref_no"),
        )

    monkeypatch.setattr(job_progress_contract_workflow, "get_job_progress_models", fake_get_job_progress_models)
    monkeypatch.setattr(
        job_progress_contract_workflow,
        "upsert_contract_record_for_progress",
        fake_upsert_contract_record_for_progress,
    )
    monkeypatch.setattr(job_progress_contract_workflow, "create_operation_log", _noop_operation_log)

    response = await job_progress_contract_workflow.update_job_progress_contract_record(
        job_id=12,
        progress_ids=[101],
        admin_user_id=1,
        db=_FakeDb(_job()),
        agreement_ref_no=None,
        update_agreement_ref_no=True,
    )

    assert captured_field_updates == {"agreement_ref_no": None}
    assert response["updated_field_keys"] == ["agreement_ref_no"]
    assert response["items"][0]["contract_record_data"]["agreement_ref_no"] is None


async def test_accepted_rate_can_be_cleared_with_explicit_null(monkeypatch: pytest.MonkeyPatch) -> None:
    progress = _progress()
    captured_field_updates: dict[str, Any] = {}

    async def fake_get_job_progress_models(**_kwargs: Any) -> list[SimpleNamespace]:
        return [progress]

    async def fake_upsert_contract_record_for_progress(**kwargs: Any) -> SimpleNamespace:
        captured_field_updates.update(kwargs["field_updates"])
        return _contract_record(
            progress=kwargs["progress"],
            rate=kwargs["field_updates"].get("rate"),
        )

    monkeypatch.setattr(job_progress_contract_workflow, "get_job_progress_models", fake_get_job_progress_models)
    monkeypatch.setattr(
        job_progress_contract_workflow,
        "upsert_contract_record_for_progress",
        fake_upsert_contract_record_for_progress,
    )
    monkeypatch.setattr(job_progress_contract_workflow, "create_operation_log", _noop_operation_log)

    response = await job_progress_contract_workflow.update_job_progress_contract_record(
        job_id=12,
        progress_ids=[101],
        admin_user_id=1,
        db=_FakeDb(_job()),
        rate=None,
        update_rate=True,
    )

    assert captured_field_updates == {"rate": None}
    assert response["updated_field_keys"] == ["rate"]
    assert response["items"][0]["contract_record_data"]["rate"] is None


async def test_onboarding_date_can_be_cleared_with_explicit_null(monkeypatch: pytest.MonkeyPatch) -> None:
    progress = _progress(
        data={
            JobProgressDataKey.ONBOARDING_STATUS.value: "已进群",
            JobProgressDataKey.ONBOARDING_DATE.value: "2026-06-24",
        }
    )

    async def fake_get_job_progress_models(**_kwargs: Any) -> list[SimpleNamespace]:
        return [progress]

    monkeypatch.setattr(job_progress_commands, "get_job_progress_models", fake_get_job_progress_models)
    monkeypatch.setattr(job_progress_commands, "create_operation_log", _noop_operation_log)

    db = _FakeDb(_job())
    response = await job_progress_commands.update_job_progress_onboarding(
        job_id=12,
        progress_ids=[101],
        admin_user_id=1,
        db=db,
        onboarding_date=None,
        update_onboarding_date=True,
    )

    assert db.flushed is True
    assert progress.data.get(JobProgressDataKey.ONBOARDING_DATE.value) is None
    assert response["updated_field_keys"] == ["onboarding_date"]
