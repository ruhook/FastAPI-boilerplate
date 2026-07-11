from ...core.exceptions.http_exceptions import ConflictException
from .const import PayableStatus

_ALLOWED_TRANSITIONS: dict[PayableStatus, frozenset[PayableStatus]] = {
    PayableStatus.PENDING: frozenset({PayableStatus.PROCESSING, PayableStatus.CANCELLED}),
    PayableStatus.PROCESSING: frozenset(
        {
            PayableStatus.PENDING,
            PayableStatus.PAID,
            PayableStatus.CANCELLED,
        }
    ),
    PayableStatus.PAID: frozenset({PayableStatus.REVERSED}),
    PayableStatus.CANCELLED: frozenset(),
    PayableStatus.REVERSED: frozenset(),
}


def ensure_payable_transition(current: PayableStatus, target: PayableStatus) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ConflictException("Payable status changed or transition is not allowed.")
