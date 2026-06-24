import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from sqlalchemy import Numeric, String, and_, cast, func, or_
from sqlalchemy.sql.elements import ColumnElement

from .exceptions.http_exceptions import BadRequestException

AdvancedFilterFieldKind = Literal["text", "email", "number", "select", "multiselect", "file", "date"]
AdvancedFilterQuery = dict[str, Any]
AdvancedFilterRecord = dict[str, Any]


@dataclass(frozen=True)
class AdvancedFilterFieldDefinition:
    name: str
    filter_kind: AdvancedFilterFieldKind
    sql_expression: ColumnElement[Any] | None = None


TEXT_OPERATORS = {"contains", "doesNotContain", "=", "!=", "isEmpty", "isNotEmpty"}
SELECT_OPERATORS = {"=", "!=", "isEmpty", "isNotEmpty"}
NUMBER_OPERATORS = {">", ">=", "<", "<=", "=", "!=", "isEmpty", "isNotEmpty"}
DATE_OPERATORS = {">", ">=", "<", "<=", "=", "!=", "isEmpty", "isNotEmpty"}
FILE_OPERATORS = {"uploaded", "notUploaded"}
MULTISELECT_OPERATORS = {"contains", "doesNotContain", "isEmpty", "isNotEmpty"}

OPERATORS_BY_KIND: dict[AdvancedFilterFieldKind, set[str]] = {
    "text": TEXT_OPERATORS,
    "email": TEXT_OPERATORS,
    "date": DATE_OPERATORS,
    "select": SELECT_OPERATORS,
    "number": NUMBER_OPERATORS,
    "file": FILE_OPERATORS,
    "multiselect": MULTISELECT_OPERATORS,
}


def _normalize_required_text(value: Any, *, error_message: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise BadRequestException(error_message)
    return normalized


def _normalize_rule_or_group(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BadRequestException("Advanced filter rule must be an object.")

    if isinstance(value.get("rules"), list):
        return _normalize_query_group(value)

    return {
        "field": _normalize_required_text(value.get("field"), error_message="Advanced filter field is required."),
        "operator": _normalize_required_text(
            value.get("operator"),
            error_message="Advanced filter operator is required.",
        ),
        "value": value.get("value"),
    }


def _normalize_query_group(value: Any) -> AdvancedFilterQuery:
    if not isinstance(value, dict):
        raise BadRequestException("Advanced filter payload must be an object.")

    combinator = str(value.get("combinator") or "and").strip().lower()
    if combinator not in {"and", "or"}:
        raise BadRequestException("Unsupported advanced filter combinator.")

    raw_rules = value.get("rules")
    if raw_rules is None:
        raw_rules = []
    if not isinstance(raw_rules, list):
        raise BadRequestException("Advanced filter rules must be an array.")

    return {
        "combinator": combinator,
        "rules": [_normalize_rule_or_group(rule) for rule in raw_rules],
    }


def parse_advanced_filter_query(raw_value: str | None) -> AdvancedFilterQuery | None:
    normalized = str(raw_value or "").strip()
    if not normalized:
        return None
    try:
        parsed = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise BadRequestException("Advanced filter payload must be valid JSON.") from exc
    return _normalize_query_group(parsed)


def has_advanced_filter_rules(query: AdvancedFilterQuery | None) -> bool:
    return bool(query and query.get("rules"))


def validate_advanced_filter_query(
    query: AdvancedFilterQuery | None,
    *,
    field_map: dict[str, AdvancedFilterFieldDefinition],
) -> None:
    if not has_advanced_filter_rules(query):
        return
    for rule in query["rules"]:
        if isinstance(rule, dict) and isinstance(rule.get("rules"), list):
            validate_advanced_filter_query(rule, field_map=field_map)
            continue

        field_name = str(rule.get("field") or "").strip()
        field_definition = field_map.get(field_name)
        if field_definition is None:
            raise BadRequestException(f"Advanced filter field '{field_name}' is not supported.")

        operator = str(rule.get("operator") or "").strip()
        allowed_operators = OPERATORS_BY_KIND.get(field_definition.filter_kind, TEXT_OPERATORS)
        if operator not in allowed_operators:
            raise BadRequestException(
                f"Advanced filter operator '{operator}' is not supported for field '{field_name}'."
            )


def _is_empty_value(value: Any) -> bool:
    if isinstance(value, list):
        return len(value) == 0
    return value is None or str(value).strip() in {"", "-"}


def _normalize_to_comparable_string(value: Any) -> str:
    if isinstance(value, list):
        return " / ".join(str(item or "").strip() for item in value)
    return str(value or "").strip()


def _evaluate_number_rule(raw_value: Any, operator: str, target_value: Any) -> bool:
    try:
        left = float(raw_value)
        right = float(target_value)
    except (TypeError, ValueError):
        return False

    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == "=":
        return left == right
    if operator == "!=":
        return left != right
    return True


def _normalize_date_value(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    normalized = _normalize_to_comparable_string(value)
    if not normalized:
        raise ValueError("Empty date value.")

    # The UI sends YYYY-MM-DD. Existing data may be full ISO datetimes, so only
    # compare on the date portion for user-facing date filters.
    return date.fromisoformat(normalized[:10])


def _evaluate_date_rule(raw_value: Any, operator: str, target_value: Any) -> bool:
    try:
        left = _normalize_date_value(raw_value)
        right = _normalize_date_value(target_value)
    except (TypeError, ValueError):
        return False

    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == "=":
        return left == right
    if operator == "!=":
        return left != right
    return True


def _evaluate_multiselect_rule(raw_value: Any, operator: str, target_value: Any) -> bool:
    if isinstance(raw_value, list):
        left_values = [_normalize_to_comparable_string(value).lower() for value in raw_value]
    else:
        left_values = [
            value.strip()
            for value in (
                _normalize_to_comparable_string(raw_value)
                .lower()
                .replace("、", ",")
                .replace("/", ",")
                .split(",")
            )
            if value.strip()
        ]

    if isinstance(target_value, list):
        target_values = [_normalize_to_comparable_string(value).lower() for value in target_value]
    else:
        target_values = [
            value.strip()
            for value in _normalize_to_comparable_string(target_value).lower().replace("、", ",").split(",")
            if value.strip()
        ]

    if operator == "contains":
        return any(value in left_values for value in target_values)
    if operator == "doesNotContain":
        return all(value not in left_values for value in target_values)
    return True


def evaluate_advanced_filter_rule(
    rule: dict[str, Any],
    *,
    record: AdvancedFilterRecord,
    field_map: dict[str, AdvancedFilterFieldDefinition],
) -> bool:
    field_name = str(rule.get("field") or "").strip()
    field_definition = field_map.get(field_name)
    if field_definition is None:
        return True

    raw_value = record.get(field_name)
    operator = str(rule.get("operator") or "").strip()
    target_value = rule.get("value")

    if operator == "uploaded":
        return not _is_empty_value(raw_value)
    if operator == "notUploaded":
        return _is_empty_value(raw_value)
    if operator == "isEmpty":
        return _is_empty_value(raw_value)
    if operator == "isNotEmpty":
        return not _is_empty_value(raw_value)

    if field_definition.filter_kind == "number":
        return _evaluate_number_rule(raw_value, operator, target_value)

    if field_definition.filter_kind == "date":
        return _evaluate_date_rule(raw_value, operator, target_value)

    if field_definition.filter_kind == "multiselect":
        return _evaluate_multiselect_rule(raw_value, operator, target_value)

    left = _normalize_to_comparable_string(raw_value).lower()
    right = _normalize_to_comparable_string(target_value).lower()
    if operator == "contains":
        return right in left
    if operator == "doesNotContain":
        return right not in left
    if operator == "=":
        return left == right
    if operator == "!=":
        return left != right
    return True


def evaluate_advanced_filter_query(
    query: AdvancedFilterQuery | None,
    *,
    record: AdvancedFilterRecord,
    field_map: dict[str, AdvancedFilterFieldDefinition],
) -> bool:
    if not has_advanced_filter_rules(query):
        return True

    results: list[bool] = []
    for rule in query["rules"]:
        if isinstance(rule, dict) and isinstance(rule.get("rules"), list):
            results.append(evaluate_advanced_filter_query(rule, record=record, field_map=field_map))
        else:
            results.append(evaluate_advanced_filter_rule(rule, record=record, field_map=field_map))

    return any(results) if query["combinator"] == "or" else all(results)


def _build_sql_string_expression(expression: ColumnElement[Any]) -> ColumnElement[Any]:
    return func.trim(func.coalesce(cast(expression, String()), ""))


def _build_sql_empty_condition(expression: ColumnElement[Any]) -> ColumnElement[bool]:
    normalized = _build_sql_string_expression(expression)
    return or_(
        expression.is_(None),
        normalized == "",
        normalized == "-",
    )


def _build_sql_number_expression(expression: ColumnElement[Any]) -> ColumnElement[Any]:
    return cast(func.nullif(_build_sql_string_expression(expression), ""), Numeric(20, 6))


def _build_sql_date_expression(expression: ColumnElement[Any]) -> ColumnElement[Any]:
    normalized = func.nullif(_build_sql_string_expression(expression), "")
    return func.substr(normalized, 1, 10)


def _build_sql_multiselect_expression(expression: ColumnElement[Any]) -> ColumnElement[Any]:
    normalized = func.lower(_build_sql_string_expression(expression))
    normalized = func.replace(normalized, "、", ",")
    normalized = func.replace(normalized, "/", ",")
    normalized = func.replace(normalized, ", ", ",")
    normalized = func.replace(normalized, " ,", ",")
    return func.concat(",", normalized, ",")


def build_advanced_filter_rule_sql_condition(  # noqa: C901
    rule: dict[str, Any],
    *,
    field_map: dict[str, AdvancedFilterFieldDefinition],
) -> ColumnElement[bool]:
    field_name = str(rule.get("field") or "").strip()
    field_definition = field_map.get(field_name)
    if field_definition is None or field_definition.sql_expression is None:
        raise BadRequestException(f"Advanced filter field '{field_name}' is not supported for SQL filtering.")

    expression = field_definition.sql_expression
    operator = str(rule.get("operator") or "").strip()
    target_value = rule.get("value")

    if operator == "uploaded":
        return ~_build_sql_empty_condition(expression)
    if operator == "notUploaded":
        return _build_sql_empty_condition(expression)
    if operator == "isEmpty":
        return _build_sql_empty_condition(expression)
    if operator == "isNotEmpty":
        return ~_build_sql_empty_condition(expression)

    if field_definition.filter_kind == "number":
        try:
            right_value = Decimal(str(target_value))
        except (TypeError, ValueError, InvalidOperation) as exc:
            raise BadRequestException(f"Advanced filter value for '{field_name}' must be numeric.") from exc
        left_value = _build_sql_number_expression(expression)
        if operator == ">":
            return left_value > right_value
        if operator == ">=":
            return left_value >= right_value
        if operator == "<":
            return left_value < right_value
        if operator == "<=":
            return left_value <= right_value
        if operator == "=":
            return left_value == right_value
        if operator == "!=":
            return left_value != right_value

    if field_definition.filter_kind == "date":
        try:
            right_value = _normalize_date_value(target_value)
        except (TypeError, ValueError) as exc:
            raise BadRequestException(
                f"Advanced filter value for '{field_name}' must be a valid date."
            ) from exc
        right_value_text = right_value.isoformat()
        left_value = _build_sql_date_expression(expression)
        if operator == ">":
            return left_value > right_value_text
        if operator == ">=":
            return left_value >= right_value_text
        if operator == "<":
            return left_value < right_value_text
        if operator == "<=":
            return left_value <= right_value_text
        if operator == "=":
            return left_value == right_value_text
        if operator == "!=":
            return left_value != right_value_text

    if field_definition.filter_kind == "multiselect":
        normalized_expression = _build_sql_multiselect_expression(expression)
        if isinstance(target_value, list):
            target_values = [_normalize_to_comparable_string(value).lower() for value in target_value]
        else:
            target_values = [
                value.strip()
                for value in _normalize_to_comparable_string(target_value).lower().replace("、", ",").split(",")
                if value.strip()
            ]
        conditions = [normalized_expression.like(f"%,{value},%") for value in target_values]
        if not conditions:
            return ~_build_sql_empty_condition(expression)
        return or_(*conditions) if operator == "contains" else and_(*[~condition for condition in conditions])

    left_value = func.lower(_build_sql_string_expression(expression))
    right_value = _normalize_to_comparable_string(target_value).lower()
    if operator == "contains":
        return left_value.like(f"%{right_value}%")
    if operator == "doesNotContain":
        return ~left_value.like(f"%{right_value}%")
    if operator == "=":
        return left_value == right_value
    if operator == "!=":
        return left_value != right_value

    raise BadRequestException(f"Advanced filter operator '{operator}' is not supported for field '{field_name}'.")


def build_advanced_filter_query_sql_condition(
    query: AdvancedFilterQuery | None,
    *,
    field_map: dict[str, AdvancedFilterFieldDefinition],
) -> ColumnElement[bool] | None:
    if not has_advanced_filter_rules(query):
        return None

    conditions: list[ColumnElement[bool]] = []
    for rule in query["rules"]:
        if isinstance(rule, dict) and isinstance(rule.get("rules"), list):
            nested_condition = build_advanced_filter_query_sql_condition(rule, field_map=field_map)
            if nested_condition is not None:
                conditions.append(nested_condition)
            continue
        conditions.append(build_advanced_filter_rule_sql_condition(rule, field_map=field_map))

    if not conditions:
        return None
    return or_(*conditions) if query["combinator"] == "or" else and_(*conditions)
