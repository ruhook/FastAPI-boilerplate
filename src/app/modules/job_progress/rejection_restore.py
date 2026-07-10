from collections.abc import Mapping
from datetime import date
from typing import Any

from .const import JobProgressDataKey, RecruitmentStage


def build_rejected_progress_data(
    process_data: Mapping[str, Any],
    *,
    source_stage: str,
    contract_status: str | None = None,
    contract_end_date: date | None = None,
) -> dict[str, Any]:
    next_data = dict(process_data)
    next_data[JobProgressDataKey.REJECTED_FROM_STAGE.value] = source_stage
    next_data.pop(JobProgressDataKey.REJECTED_CONTRACT_PREVIOUS_STATUS.value, None)
    next_data.pop(JobProgressDataKey.REJECTED_CONTRACT_PREVIOUS_END_DATE.value, None)

    if source_stage == RecruitmentStage.ACTIVE.value:
        next_data[JobProgressDataKey.REJECTED_CONTRACT_PREVIOUS_STATUS.value] = contract_status
        next_data[JobProgressDataKey.REJECTED_CONTRACT_PREVIOUS_END_DATE.value] = (
            contract_end_date.isoformat() if contract_end_date is not None else None
        )
    return next_data


def pop_active_contract_restore_data(
    process_data: Mapping[str, Any],
) -> tuple[dict[str, Any], str | None, date | None]:
    next_data = dict(process_data)
    previous_status = next_data.pop(JobProgressDataKey.REJECTED_CONTRACT_PREVIOUS_STATUS.value, None)
    raw_end_date = next_data.pop(JobProgressDataKey.REJECTED_CONTRACT_PREVIOUS_END_DATE.value, None)
    next_data.pop(JobProgressDataKey.REJECTED_FROM_STAGE.value, None)

    previous_end_date = date.fromisoformat(str(raw_end_date)) if raw_end_date else None
    return next_data, str(previous_status) if previous_status else None, previous_end_date
