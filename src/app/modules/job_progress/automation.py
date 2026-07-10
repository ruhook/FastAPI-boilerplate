from typing import Any

from ..candidate_application_field_value.model import CandidateApplicationFieldValue
from ..job.const import JOB_DATA_AUTOMATION_RULES_KEY
from ..job.model import Job
from .const import RecruitmentScreeningMode, RecruitmentStage
from .normalization import _normalize_number, _normalize_text


def _build_field_value_map(
    field_rows: list[CandidateApplicationFieldValue],
) -> dict[str, dict[str, Any]]:
    value_map: dict[str, dict[str, Any]] = {}
    for row in field_rows:
        key = row.catalog_key or row.field_key
        value_map[key] = {
            "raw_value": row.raw_value,
            "display_value": row.display_value,
            "asset_id": row.asset_id,
        }
    return value_map


def _evaluate_automation_rule(
    rule: dict[str, Any],
    field_values: dict[str, dict[str, Any]],
) -> bool:
    field_key = _normalize_text(rule.get("fieldKey"))
    operator = _normalize_text(rule.get("operator")).lower()
    configured_value = rule.get("value")
    field_entry = field_values.get(field_key, {})
    display_value = field_entry.get("display_value")
    raw_value = field_entry.get("raw_value")
    asset_id = field_entry.get("asset_id")
    actual_value = raw_value if raw_value is not None else display_value
    raw_text = _normalize_text(raw_value).lower()
    display_text = _normalize_text(display_value).lower()

    if operator == "uploaded":
        return asset_id is not None or _normalize_text(actual_value) != ""
    if operator == "not_uploaded":
        return asset_id is None and _normalize_text(actual_value) == ""
    if operator == "true":
        return _normalize_text(actual_value).lower() in {"true", "1", "yes"}
    if operator == "false":
        return _normalize_text(actual_value).lower() in {"false", "0", "no"}

    if operator in {"gt", "lt", "between"}:
        left = _normalize_number(actual_value)
        if left is None:
            return False
        if operator == "gt":
            right = _normalize_number(configured_value)
            return right is not None and left > right
        if operator == "lt":
            right = _normalize_number(configured_value)
            return right is not None and left < right
        if operator == "between":
            lower = _normalize_number(configured_value)
            upper = _normalize_number(rule.get("secondValue"))
            return lower is not None and upper is not None and lower <= left <= upper

    actual_text = _normalize_text(actual_value).lower()
    normalized_actual_parts = {
        value.strip().lower()
        for source in {actual_text, raw_text, display_text}
        for value in source.replace("/", ",").split(",")
        if value.strip()
    }
    if operator == "contains":
        target = _normalize_text(configured_value).lower()
        return any(target in source for source in {actual_text, raw_text, display_text})
    if operator == "not_contains":
        target = _normalize_text(configured_value).lower()
        return all(target not in source for source in {actual_text, raw_text, display_text})
    if operator == "includes":
        target_values = configured_value if isinstance(configured_value, list) else [configured_value]
        return any(_normalize_text(item).lower() in normalized_actual_parts for item in target_values)
    if operator == "not_includes":
        target_values = configured_value if isinstance(configured_value, list) else [configured_value]
        return all(_normalize_text(item).lower() not in normalized_actual_parts for item in target_values)
    if operator == "eq":
        left_number = _normalize_number(actual_value)
        right_number = _normalize_number(configured_value)
        if left_number is not None and right_number is not None:
            return left_number == right_number
        target = _normalize_text(configured_value).lower()
        return target in {actual_text, raw_text, display_text}

    return False


def _evaluate_automation_rules(
    job: Job,
    field_rows: list[CandidateApplicationFieldValue],
) -> tuple[bool, bool]:
    data = job.data or {}
    rule_group = data.get(JOB_DATA_AUTOMATION_RULES_KEY) or {}
    rules = list(rule_group.get("rules") or [])
    if not rules:
        return False, False

    combinator = _normalize_text(rule_group.get("combinator") or "and").lower()
    field_values = _build_field_value_map(field_rows)
    normalized_rules = [rule for rule in rules if isinstance(rule, dict)]

    def is_any_group(rule: dict[str, Any]) -> bool:
        return _normalize_text(rule.get("group")).lower() in {"any", "or"}

    any_group_rules = [rule for rule in normalized_rules if is_any_group(rule)]
    if any_group_rules:
        required_results = [
            _evaluate_automation_rule(rule, field_values) for rule in normalized_rules if not is_any_group(rule)
        ]
        any_results = [_evaluate_automation_rule(rule, field_values) for rule in any_group_rules]
        matched = all(required_results) and (not any_results or any(any_results))
        return True, matched

    results = [_evaluate_automation_rule(rule, field_values) for rule in normalized_rules]
    if not results:
        return False, False
    matched = all(results) if combinator != "or" else any(results)
    return True, matched


def _resolve_initial_stage(
    *,
    job: Job,
    field_rows: list[CandidateApplicationFieldValue],
) -> tuple[RecruitmentStage, RecruitmentScreeningMode, str, bool]:
    auto_screening_enabled, matched = _evaluate_automation_rules(job, field_rows)
    if not auto_screening_enabled:
        return (
            RecruitmentStage.PENDING_SCREENING,
            RecruitmentScreeningMode.MANUAL,
            "岗位未配置自动筛选规则，申请停留在待筛选名单。",
            False,
        )

    if matched:
        return (
            RecruitmentStage.PENDING_SCREENING,
            RecruitmentScreeningMode.AUTO,
            "自动筛选通过，申请停留在待筛选名单，等待测试题提交或人工处理。",
            True,
        )
    return (
        RecruitmentStage.PENDING_SCREENING,
        RecruitmentScreeningMode.AUTO,
        "自动筛选未通过，申请保留在待筛选名单等待人工处理。",
        False,
    )


def _field_row_value(field_rows: list[CandidateApplicationFieldValue], key: str) -> str:
    for row in field_rows:
        row_key = row.catalog_key or row.field_key
        if row_key == key:
            return _normalize_text(row.display_value if row.display_value is not None else row.raw_value)
    return ""
