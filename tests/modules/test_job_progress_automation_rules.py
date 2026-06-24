from types import SimpleNamespace
from typing import Any

from src.app.modules.job.const import JOB_DATA_AUTOMATION_RULES_KEY
from src.app.modules.job_progress.service import _evaluate_automation_rules


def _job_with_rules(rule_group: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(data={JOB_DATA_AUTOMATION_RULES_KEY: rule_group})


def _field_value(field_key: str, value: str) -> SimpleNamespace:
    return SimpleNamespace(
        catalog_key=None,
        field_key=field_key,
        raw_value=value,
        display_value=value,
        asset_id=None,
    )


def _rule(
    *,
    field_key: str,
    operator: str,
    value: str | list[str],
    group: str = "required",
) -> dict[str, Any]:
    return {
        "fieldKey": field_key,
        "fieldLabel": field_key,
        "fieldType": "text",
        "operator": operator,
        "group": group,
        "value": value,
    }


def test_or_combinator_honors_required_group_defaults() -> None:
    enabled, matched = _evaluate_automation_rules(
        _job_with_rules(
            {
                "combinator": "or",
                "rules": [
                    _rule(field_key="nationality", operator="contains", value="Malaysian"),
                    _rule(field_key="education_status", operator="contains", value="Bachelor"),
                ],
            }
        ),
        [
            _field_value("nationality", "Brazilian"),
            _field_value("education_status", "Bachelor's degree"),
        ],
    )

    assert enabled is True
    assert matched is True


def test_required_rules_and_one_any_rule_can_match_multi_value_includes() -> None:
    enabled, matched = _evaluate_automation_rules(
        _job_with_rules(
            {
                "combinator": "and",
                "rules": [
                    _rule(field_key="education_status", operator="contains", value="Bachelor", group="required"),
                    _rule(
                        field_key="country_of_residence",
                        operator="includes",
                        value=["Malaysia", "Singapore"],
                        group="any",
                    ),
                    _rule(field_key="nationality", operator="includes", value=["Malaysian"], group="any"),
                ],
            }
        ),
        [
            _field_value("education_status", "Bachelor's degree"),
            _field_value("country_of_residence", "Malaysia"),
            _field_value("nationality", "Brazilian"),
        ],
    )

    assert enabled is True
    assert matched is True


def test_required_rules_pass_but_no_any_rule_matches_fails() -> None:
    enabled, matched = _evaluate_automation_rules(
        _job_with_rules(
            {
                "combinator": "and",
                "rules": [
                    _rule(field_key="education_status", operator="contains", value="Bachelor", group="required"),
                    _rule(
                        field_key="country_of_residence",
                        operator="includes",
                        value=["Malaysia", "Singapore"],
                        group="any",
                    ),
                    _rule(field_key="nationality", operator="includes", value=["Malaysian"], group="any"),
                ],
            }
        ),
        [
            _field_value("education_status", "Bachelor's degree"),
            _field_value("country_of_residence", "Brazil"),
            _field_value("nationality", "Brazilian"),
        ],
    )

    assert enabled is True
    assert matched is False
