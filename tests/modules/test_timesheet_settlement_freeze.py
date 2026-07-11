from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from src.app.core.db.database import local_session
from src.app.core.exceptions.http_exceptions import ConflictException
from src.app.modules.admin.company.model import AdminCompany, AdminCompanyProject
from src.app.modules.payable.const import PayableStatus
from src.app.modules.payable.model import Payable, PayableTimesheetSource
from src.app.modules.payable.source_policy import ensure_timesheets_editable
from src.app.modules.project_timesheet_record.model import ProjectTimesheetRecord
from src.app.modules.user.model import User

pytestmark = pytest.mark.no_database_cleanup


@pytest.mark.asyncio(loop_scope="session")
async def test_only_pending_payable_sources_remain_editable() -> None:
    suffix = uuid4().hex[:10]
    statuses = (
        PayableStatus.PENDING,
        PayableStatus.PROCESSING,
        PayableStatus.PAID,
        PayableStatus.REVERSED,
    )

    async with local_session() as setup:
        company = AdminCompany(name=f"Settlement Freeze {suffix}", description=None, data={})
        setup.add(company)
        await setup.flush()
        project = AdminCompanyProject(company_id=company.id, name=f"Project {suffix}", data={})
        user = User(
            name="Settlement Worker",
            username=f"sf{suffix}"[:20],
            email=f"sf.{suffix}@example.com",
            hashed_password="test-hash",
            profile_image_url="https://example.com/profile.png",
            data={},
        )
        setup.add_all([project, user])
        await setup.flush()

        record_ids: dict[PayableStatus, int] = {}
        for index, status in enumerate(statuses, start=1):
            record = ProjectTimesheetRecord(
                company_id=company.id,
                project_id=project.id,
                sub_project_name=f"Freeze {status.value}",
                work_date=date(2026, 7, index),
                user_id=user.id,
                language="English",
                work_type="Production",
                output_quantity=Decimal("1.00"),
                customer_duration_hours=Decimal("1.00"),
                candidate_duration_hours=Decimal("1.00"),
                non_operational_duration_hours=Decimal("0.00"),
                data={},
            )
            setup.add(record)
            await setup.flush()
            payable = Payable(
                source_key=f"freeze:{suffix}:{status.value}",
                payment_type="salary",
                status=status.value,
                settlement_month="2026-07",
                user_id=user.id,
                company_id=company.id,
                project_id=project.id,
                amount=Decimal("10.00"),
                currency="USD",
                calculation_snapshot={},
            )
            setup.add(payable)
            await setup.flush()
            setup.add(
                PayableTimesheetSource(
                    payable_id=payable.id,
                    project_timesheet_record_id=record.id,
                    source_version=record.version,
                    work_hours_snapshot=record.candidate_duration_hours,
                    amount_contribution_snapshot=Decimal("10.00"),
                )
            )
            record_ids[status] = record.id
        await setup.commit()

    async with local_session() as db:
        await ensure_timesheets_editable(db, [record_ids[PayableStatus.PENDING]])

        for status in (PayableStatus.PROCESSING, PayableStatus.PAID, PayableStatus.REVERSED):
            with pytest.raises(ConflictException, match="locked by settlement"):
                await ensure_timesheets_editable(db, [record_ids[status]])
