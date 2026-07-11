import pytest

from src.app.modules.candidate_field.const import CandidateFieldKey
from src.app.modules.candidate_field.global_dictionary_options import GLOBAL_COUNTRY_OPTIONS
from src.scripts import run_client_apply_demo, seed_job_progress_demo_flow

pytestmark = pytest.mark.no_database_cleanup


COUNTRY_VALUES = {str(option["value"]) for option in GLOBAL_COUNTRY_OPTIONS}


def _assert_country_fields_are_dictionary_values(items: list[dict[str, object]]) -> None:
    values = {str(item["field_key"]): item["value"] for item in items}

    for field_key in (
        CandidateFieldKey.NATIONALITY.value,
        CandidateFieldKey.COUNTRY_OF_RESIDENCE.value,
    ):
        assert values[field_key] == "Brazil"
        assert values[field_key] in COUNTRY_VALUES


def test_client_apply_demo_uses_country_dictionary_values() -> None:
    items = run_client_apply_demo.build_application_items(
        job_index=0,
        candidate_name="Demo Candidate",
        email="demo@example.com",
        resume_asset_id=1,
    )

    _assert_country_fields_are_dictionary_values(items)


def test_job_progress_demo_uses_country_dictionary_values() -> None:
    items = seed_job_progress_demo_flow.build_application_items(
        scenario_key="assessment_auto_pass",
        candidate_name="Progress Candidate",
        candidate_email="progress@example.com",
        resume_asset_id=2,
    )

    _assert_country_fields_are_dictionary_values(items)
