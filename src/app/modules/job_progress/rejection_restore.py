from collections.abc import Mapping
from typing import Any

from .const import JobProgressDataKey


def build_rejected_progress_data(
    process_data: Mapping[str, Any],
    *,
    source_stage: str,
) -> dict[str, Any]:
    next_data = dict(process_data)
    next_data[JobProgressDataKey.REJECTED_FROM_STAGE.value] = source_stage
    return next_data
