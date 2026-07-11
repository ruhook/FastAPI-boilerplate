from typing import TypeVar

from ...core.exceptions.http_exceptions import ConflictException
from .const import ContractReviewStatus, ContractSigningStatus, ContractStatus

_SIGNING_TRANSITIONS: dict[ContractSigningStatus, set[ContractSigningStatus]] = {
    ContractSigningStatus.NOT_SENT: {ContractSigningStatus.SENT},
    ContractSigningStatus.SENT: {ContractSigningStatus.CANDIDATE_SIGNED},
    ContractSigningStatus.CANDIDATE_SIGNED: {ContractSigningStatus.COMPANY_SEALED},
    ContractSigningStatus.COMPANY_SEALED: set(),
}

_REVIEW_TRANSITIONS: dict[ContractReviewStatus, set[ContractReviewStatus]] = {
    ContractReviewStatus.PENDING: {
        ContractReviewStatus.CHANGES_REQUESTED,
        ContractReviewStatus.APPROVED,
    },
    ContractReviewStatus.CHANGES_REQUESTED: {
        ContractReviewStatus.PENDING,
        ContractReviewStatus.APPROVED,
    },
    ContractReviewStatus.APPROVED: set(),
}

_STATUS_TRANSITIONS: dict[ContractStatus, set[ContractStatus]] = {
    ContractStatus.PENDING_ACTIVATION: {
        ContractStatus.ACTIVE,
        ContractStatus.TERMINATED,
        ContractStatus.EXPIRED,
    },
    ContractStatus.ACTIVE: {ContractStatus.TERMINATED, ContractStatus.EXPIRED},
    ContractStatus.TERMINATED: set(),
    ContractStatus.EXPIRED: set(),
}

_StateT = TypeVar("_StateT")


def _ensure_transition(
    current: _StateT,
    target: _StateT,
    transitions: dict[_StateT, set[_StateT]],
    label: str,
) -> None:
    if current == target:
        return
    if target not in transitions[current]:
        raise ConflictException(f"Invalid {label} transition from {current} to {target}.")


def ensure_signing_transition(current: ContractSigningStatus, target: ContractSigningStatus) -> None:
    _ensure_transition(current, target, _SIGNING_TRANSITIONS, "contract signing")


def ensure_review_transition(current: ContractReviewStatus, target: ContractReviewStatus) -> None:
    _ensure_transition(current, target, _REVIEW_TRANSITIONS, "contract review")


def ensure_status_transition(current: ContractStatus, target: ContractStatus) -> None:
    _ensure_transition(current, target, _STATUS_TRANSITIONS, "contract status")


def ensure_activation_allowed(
    *,
    contract_status: ContractStatus,
    review_status: ContractReviewStatus,
    signing_status: ContractSigningStatus,
) -> None:
    if review_status != ContractReviewStatus.APPROVED:
        raise ConflictException("Contract activation requires review approval.")
    if signing_status != ContractSigningStatus.COMPANY_SEALED:
        raise ConflictException("Contract activation requires a company seal.")
    ensure_status_transition(contract_status, ContractStatus.ACTIVE)
