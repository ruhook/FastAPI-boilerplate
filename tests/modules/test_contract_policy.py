import pytest

from src.app.core.exceptions.http_exceptions import ConflictException
from src.app.modules.contract_record.const import (
    ContractReviewStatus,
    ContractSigningStatus,
    ContractStatus,
)
from src.app.modules.contract_record.policy import (
    ensure_activation_allowed,
    ensure_review_transition,
    ensure_signing_transition,
    ensure_status_transition,
)

pytestmark = pytest.mark.no_database_cleanup


def test_contract_activation_requires_approval_and_company_seal() -> None:
    ensure_activation_allowed(
        contract_status=ContractStatus.PENDING_ACTIVATION,
        review_status=ContractReviewStatus.APPROVED,
        signing_status=ContractSigningStatus.COMPANY_SEALED,
    )

    with pytest.raises(ConflictException, match="review approval"):
        ensure_activation_allowed(
            contract_status=ContractStatus.PENDING_ACTIVATION,
            review_status=ContractReviewStatus.PENDING,
            signing_status=ContractSigningStatus.COMPANY_SEALED,
        )
    with pytest.raises(ConflictException, match="company seal"):
        ensure_activation_allowed(
            contract_status=ContractStatus.PENDING_ACTIVATION,
            review_status=ContractReviewStatus.APPROVED,
            signing_status=ContractSigningStatus.CANDIDATE_SIGNED,
        )


def test_contract_workflow_transition_tables_reject_skips() -> None:
    ensure_signing_transition(ContractSigningStatus.NOT_SENT, ContractSigningStatus.SENT)
    ensure_signing_transition(ContractSigningStatus.SENT, ContractSigningStatus.CANDIDATE_SIGNED)
    ensure_signing_transition(ContractSigningStatus.CANDIDATE_SIGNED, ContractSigningStatus.COMPANY_SEALED)
    ensure_review_transition(ContractReviewStatus.PENDING, ContractReviewStatus.APPROVED)
    ensure_status_transition(ContractStatus.PENDING_ACTIVATION, ContractStatus.ACTIVE)
    ensure_status_transition(ContractStatus.ACTIVE, ContractStatus.TERMINATED)

    with pytest.raises(ConflictException):
        ensure_signing_transition(ContractSigningStatus.NOT_SENT, ContractSigningStatus.COMPANY_SEALED)
    with pytest.raises(ConflictException):
        ensure_status_transition(ContractStatus.TERMINATED, ContractStatus.ACTIVE)
