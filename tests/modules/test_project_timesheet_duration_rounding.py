from decimal import Decimal

import pytest

from src.app.modules.project_timesheet_record.serialization import (
    _quantize_candidate_duration_hours,
    _quantize_customer_duration_hours,
)

pytestmark = pytest.mark.no_database_cleanup


def test_timesheet_duration_rounding_rules() -> None:
    assert _quantize_customer_duration_hours(Decimal("8.143")) == Decimal("8.15")
    assert _quantize_candidate_duration_hours(Decimal("8.143")) == Decimal("8.14")
