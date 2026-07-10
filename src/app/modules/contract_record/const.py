from decimal import ROUND_HALF_UP, Decimal

CONTRACT_STATUS_PENDING_ACTIVATION = "Pending Activation"
CONTRACT_STATUS_ACTIVE = "Active"
CONTRACT_STATUS_TERMINATED = "Terminated"
CONTRACT_STATUS_EXPIRED = "Expired"

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


def normalize_contract_type(value: str | None) -> str:
    text = (value or "").strip()
    return text if text in CONTRACT_TYPES else CONTRACT_TYPE_NORMAL


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(TWO_DECIMALS, rounding=ROUND_HALF_UP)
