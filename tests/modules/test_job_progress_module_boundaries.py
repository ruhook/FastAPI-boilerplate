import pytest
from pydantic import ValidationError

from src.app.modules.job_progress.schema import (
    ContractRecordDataRead,
    JobProgressContractRecordUpdateRequest,
)

pytestmark = pytest.mark.no_database_cleanup


def test_job_progress_contract_update_rejects_workflow_state_copies() -> None:
    fields = JobProgressContractRecordUpdateRequest.model_fields
    assert "signing_status" not in fields
    assert "contract_review" not in fields
    assert "contract_review_status" not in fields

    with pytest.raises(ValidationError):
        JobProgressContractRecordUpdateRequest.model_validate(
            {
                "progress_ids": [1],
                "signing_status": "company_sealed",
            }
        )


def test_job_progress_contract_projection_uses_typed_contract_fields() -> None:
    fields = ContractRecordDataRead.model_fields
    assert "contract_review_status" in fields
    assert "contract_review" not in fields
    assert "signing_status" in fields
