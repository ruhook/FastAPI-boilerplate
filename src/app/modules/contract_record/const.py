from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum


class ContractStatus(StrEnum):
    PENDING_ACTIVATION = "pending_activation"
    ACTIVE = "active"
    TERMINATED = "terminated"
    EXPIRED = "expired"


class ContractReviewStatus(StrEnum):
    PENDING = "pending"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"


class ContractSigningStatus(StrEnum):
    NOT_SENT = "not_sent"
    SENT = "sent"
    CANDIDATE_SIGNED = "candidate_signed"
    COMPANY_SEALED = "company_sealed"


CONTRACT_STATUS_PENDING_ACTIVATION = ContractStatus.PENDING_ACTIVATION.value
CONTRACT_STATUS_ACTIVE = ContractStatus.ACTIVE.value
CONTRACT_STATUS_TERMINATED = ContractStatus.TERMINATED.value
CONTRACT_STATUS_EXPIRED = ContractStatus.EXPIRED.value

CONTRACT_STATUSES = {
    CONTRACT_STATUS_PENDING_ACTIVATION,
    CONTRACT_STATUS_ACTIVE,
    CONTRACT_STATUS_TERMINATED,
    CONTRACT_STATUS_EXPIRED,
}

INACTIVE_CONTRACT_STATUSES = {
    CONTRACT_STATUS_TERMINATED,
    CONTRACT_STATUS_EXPIRED,
}

CONTRACT_TYPE_NORMAL = "normal"
CONTRACT_TYPE_TEAM_LEADER = "team_leader"

CONTRACT_TYPES = {
    CONTRACT_TYPE_NORMAL,
    CONTRACT_TYPE_TEAM_LEADER,
}

TWO_DECIMALS = Decimal("0.01")


def normalize_contract_status(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return CONTRACT_STATUS_PENDING_ACTIVATION
    if text not in CONTRACT_STATUSES:
        raise ValueError("Invalid contract status.")
    return text


def normalize_contract_review_status(value: str | None) -> str:
    text = (value or "").strip() or ContractReviewStatus.PENDING.value
    if text not in ContractReviewStatus:
        raise ValueError("Invalid contract review status.")
    return text


def normalize_contract_signing_status(value: str | None) -> str:
    text = (value or "").strip() or ContractSigningStatus.NOT_SENT.value
    if text not in ContractSigningStatus:
        raise ValueError("Invalid contract signing status.")
    return text


def normalize_contract_type(value: str | None) -> str:
    text = (value or "").strip()
    return text if text in CONTRACT_TYPES else CONTRACT_TYPE_NORMAL


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP)
