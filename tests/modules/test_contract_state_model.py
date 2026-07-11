import pytest

from src.app.modules.contract_record.const import (
    ContractReviewStatus,
    ContractSigningStatus,
    ContractStatus,
)
from src.app.modules.contract_record.model import ContractRecord
from src.app.modules.contract_record.schema import ContractRecordListItemRead

pytestmark = pytest.mark.no_database_cleanup


def test_contract_workflow_states_are_typed_columns() -> None:
    columns = ContractRecord.__table__.c

    assert columns.contract_status.default.arg == ContractStatus.PENDING_ACTIVATION.value
    assert columns.contract_review_status.default.arg == ContractReviewStatus.PENDING.value
    assert columns.signing_status.default.arg == ContractSigningStatus.NOT_SENT.value
    assert columns.contract_status.index is True
    assert columns.contract_review_status.index is True
    assert columns.signing_status.index is True


def test_contract_response_exposes_typed_review_field_only() -> None:
    fields = ContractRecordListItemRead.model_fields

    assert "contract_review_status" in fields
    assert "contract_review" not in fields
