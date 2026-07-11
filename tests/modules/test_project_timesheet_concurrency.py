from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from src.app.core.db.database import local_session
from src.app.core.exceptions.http_exceptions import ConflictException
from src.app.modules.admin.company.model import AdminCompany, AdminCompanyProject
from src.app.modules.project_timesheet_record.commands import flush_timesheet_write
from src.app.modules.project_timesheet_record.model import ProjectTimesheetRecord
from src.app.modules.user.model import User

pytestmark = pytest.mark.no_database_cleanup


def test_timesheet_has_optimistic_version_column() -> None:
    column = ProjectTimesheetRecord.__table__.c.version

    assert column.nullable is False
    assert column.default is not None
    assert column.default.arg == 1
    assert ProjectTimesheetRecord.__mapper__.version_id_col is column


@pytest.mark.asyncio(loop_scope="session")
async def test_stale_timesheet_writer_gets_conflict() -> None:
    suffix = uuid4().hex[:10]
    async with local_session() as setup:
        company = AdminCompany(name=f"Timesheet Concurrency {suffix}", description=None, data={})
        setup.add(company)
        await setup.flush()
        project = AdminCompanyProject(company_id=company.id, name=f"Project {suffix}", data={})
        user = User(
            name="Timesheet Writer",
            username=f"ts{suffix}"[:20],
            email=f"ts.{suffix}@example.com",
            hashed_password="test-hash",
            profile_image_url="https://example.com/profile.png",
            data={},
        )
        setup.add_all([project, user])
        await setup.flush()
        record = ProjectTimesheetRecord(
            company_id=company.id,
            project_id=project.id,
            sub_project_name="Concurrency",
            work_date=date(2026, 7, 11),
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
        await setup.commit()
        record_id = record.id

    async with local_session() as first_session, local_session() as second_session:
        first = await first_session.get(ProjectTimesheetRecord, record_id)
        second = await second_session.get(ProjectTimesheetRecord, record_id)
        assert first is not None and second is not None

        first.extra_notes = "first writer"
        await flush_timesheet_write(first_session)
        await first_session.commit()

        second.extra_notes = "stale writer"
        with pytest.raises(ConflictException, match="changed by another request"):
            await flush_timesheet_write(second_session)
