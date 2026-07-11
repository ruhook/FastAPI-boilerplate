import pytest

from src.app.core.advanced_filter import build_advanced_filter_query_sql_condition
from src.app.modules.contract_record import queries as contract_queries

pytestmark = pytest.mark.no_database_cleanup


def test_contract_advanced_filter_field_map_supports_table_fields() -> None:
    field_map = contract_queries._build_contract_advanced_filter_field_map()

    assert field_map["contractor_name"].filter_kind == "text"
    assert field_map["contractor_email"].filter_kind == "email"
    assert field_map["contract_status"].filter_kind == "select"
    assert field_map["contract_review_status"].filter_kind == "select"
    assert field_map["signing_status"].filter_kind == "select"
    assert field_map["contract_type"].filter_kind == "select"
    assert field_map["rate"].filter_kind == "number"
    assert field_map["effective_date"].filter_kind == "date"
    assert field_map["contract_attachment"].filter_kind == "file"
    assert field_map["id_attachment"].filter_kind == "file"


def test_contract_advanced_filter_field_map_builds_sql_conditions() -> None:
    field_map = contract_queries._build_contract_advanced_filter_field_map()
    query = {
        "combinator": "and",
        "rules": [
            {"field": "contract_status", "operator": "=", "value": "active"},
            {"field": "rate", "operator": ">=", "value": 5},
            {"field": "contract_attachment", "operator": "uploaded", "value": ""},
        ],
    }

    condition = build_advanced_filter_query_sql_condition(query, field_map=field_map)

    assert condition is not None
