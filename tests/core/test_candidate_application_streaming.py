from datetime import UTC, datetime

import pytest

from src.app.modules.candidate_application.model import CandidateApplication
from src.app.modules.contract_record.model import ContractRecord
from src.app.modules.job.model import Job
from src.app.modules.job_progress.const import JobProgressDataKey
from src.app.modules.job_progress.model import JobProgress
from src.app.modules.job_progress.service import list_candidate_job_applications

pytestmark = pytest.mark.no_database_cleanup


def build_progress(*, progress_id: int, stage: str, data: dict | None = None) -> JobProgress:
    return JobProgress(
        id=progress_id,
        job_id=10,
        user_id=7,
        application_id=progress_id,
        current_stage=stage,
        screening_mode="manual",
        data=data or {},
    )


def build_application(application_id: int) -> CandidateApplication:
    return CandidateApplication(
        id=application_id,
        user_id=7,
        job_id=10,
        job_snapshot_title=f"Job {application_id}",
        submitted_at=datetime.now(UTC),
        data={},
    )


def build_job(*, assessment_enabled: bool = False) -> Job:
    return Job(
        id=10,
        title="Streaming Job",
        country="Brazil",
        status="在招",
        work_mode="Remote",
        assessment_enabled=assessment_enabled,
        applicant_count=3,
        data={},
    )


def build_contract(progress_id: int) -> ContractRecord:
    return ContractRecord(
        id=30,
        user_id=7,
        job_id=10,
        job_progress_id=progress_id,
        service_customer_project_id=1,
        contract_status="Pending Activation",
        legal_entity="T-Maxx International",
        worker_type="Contractor",
        draft_contract_asset_id=99,
        is_current=True,
        data={},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("needs_action_only", "expected_total", "expected_summary"),
    [
        (
            False,
            3,
            {
                "contract_uploads": 1,
                "other_actions": 1,
                "monitoring": 1,
                "total_action_required": 2,
            },
        ),
        (
            True,
            2,
            {
                "contract_uploads": 1,
                "other_actions": 1,
                "monitoring": 0,
                "total_action_required": 2,
            },
        ),
    ],
)
async def test_candidate_application_summary_streams_without_materializing_all_rows(
    needs_action_only: bool,
    expected_total: int,
    expected_summary: dict[str, int],
) -> None:
    review_progress = build_progress(progress_id=1, stage="pending_screening")
    assessment_progress = build_progress(
        progress_id=2,
        stage="pending_screening",
        data={JobProgressDataKey.ASSESSMENT_SENT_AT.value: "2026-07-10T10:00:00Z"},
    )
    contract_progress = build_progress(progress_id=3, stage="contract_pool")
    rows = [
        (review_progress, build_application(1), build_job(), None),
        (assessment_progress, build_application(2), build_job(assessment_enabled=True), None),
        (contract_progress, build_application(3), build_job(), build_contract(3)),
    ]

    class AsyncRows:
        def __aiter__(self):
            async def iterate():
                for row in rows:
                    yield row

            return iterate()

    class StreamingDatabase:
        stream_count = 0

        async def stream(self, _statement):
            self.stream_count += 1
            return AsyncRows()

        async def execute(self, _statement):
            raise AssertionError("empty result page should not execute follow-up materialization queries")

    database = StreamingDatabase()
    result = await list_candidate_job_applications(
        user_id=7,
        page=99,
        page_size=20,
        needs_action_only=needs_action_only,
        db=database,  # type: ignore[arg-type]
    )

    assert database.stream_count == 1
    assert result["items"] == []
    assert result["total"] == expected_total
    assert result["summary"] == expected_summary
