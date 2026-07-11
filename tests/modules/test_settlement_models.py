import pytest

from src.app.modules.payable.const import PayableStatus
from src.app.modules.payable.model import Payable, PayableTimesheetSource
from src.app.modules.payment.const import PaymentEntryType
from src.app.modules.payment.model import Payment

pytestmark = pytest.mark.no_database_cleanup


def test_payable_source_key_and_payment_entries_are_unique() -> None:
    assert Payable.__table__.c.source_key.unique is True

    payment_constraints = {constraint.name for constraint in Payment.__table__.constraints}
    assert "uq_payment_payable_entry_type" in payment_constraints
    assert "uq_payment_reversal_of" in payment_constraints

    source_constraints = {constraint.name for constraint in PayableTimesheetSource.__table__.constraints}
    assert "uq_payable_timesheet_source" in source_constraints


def test_payable_uses_optimistic_versioning() -> None:
    column = Payable.__table__.c.version

    assert column.nullable is False
    assert column.default is not None
    assert column.default.arg == 1
    assert Payable.__mapper__.version_id_col is column


def test_settlement_state_values_are_stable() -> None:
    assert {status.value for status in PayableStatus} == {
        "pending",
        "processing",
        "paid",
        "cancelled",
        "reversed",
    }
    assert {entry_type.value for entry_type in PaymentEntryType} == {"payment", "reversal"}
