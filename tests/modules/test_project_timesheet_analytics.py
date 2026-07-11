from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.app.modules.project_timesheet_record.analytics import _build_timesheet_analytics_summary

pytestmark = pytest.mark.no_database_cleanup


def test_build_timesheet_analytics_summary_maps_all_ten_query_columns() -> None:
    latest_at = datetime(2026, 7, 11, tzinfo=UTC)

    summary = _build_timesheet_analytics_summary(
        (
            7,
            2,
            3,
            4,
            5,
            Decimal("12.345"),
            Decimal("6.781"),
            Decimal("6.789"),
            Decimal("1.234"),
            latest_at,
        )
    )

    assert summary.record_count == 7
    assert summary.company_count == 2
    assert summary.project_count == 3
    assert summary.person_count == 4
    assert summary.sub_project_count == 5
    assert summary.output_quantity == Decimal("12.35")
    assert summary.customer_duration_hours == Decimal("6.79")
    assert summary.candidate_duration_hours == Decimal("6.79")
    assert summary.non_operational_duration_hours == Decimal("1.23")
    assert summary.latest_created_at == latest_at
