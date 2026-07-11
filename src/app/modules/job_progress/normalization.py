from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, overload

from .const import JobProgressDataKey
from .model import JobProgress


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_language_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item for item in (_normalize_text(item) for item in value) if item]
    normalized = _normalize_text(value)
    return [normalized] if normalized else []


def _has_asset_id(value: Any) -> bool:
    return _normalize_text(value).lower() not in {"", "0", "none", "null"}


def _has_assessment_attachment(progress: JobProgress) -> bool:
    progress_data = progress.data or {}
    raw_submissions = progress_data.get(JobProgressDataKey.ASSESSMENT_SUBMISSIONS.value)
    if not isinstance(raw_submissions, list):
        return False
    return any(isinstance(item, dict) and _has_asset_id(item.get("asset_id")) for item in raw_submissions)


def _normalize_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _normalize_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip())
    except Exception:
        return None


@overload
def _ensure_utc_datetime(value: datetime) -> datetime: ...


@overload
def _ensure_utc_datetime(value: None) -> None: ...


def _ensure_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _serialize_progress_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()
