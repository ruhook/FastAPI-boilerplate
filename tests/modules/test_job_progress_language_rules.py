import pytest

from src.app.modules.job_progress.language_rules import (
    DEFAULT_PROGRESS_LANGUAGE,
    normalize_progress_language_value,
    resolve_progress_language,
)

pytestmark = pytest.mark.no_database_cleanup


def test_resolve_progress_language_matches_country_and_native_language() -> None:
    assert (
        resolve_progress_language(
            job_country="Indonesia",
            job_language_requirements=["Indonesian"],
            candidate_country_of_residence="Indonesia",
            candidate_native_languages="Indonesian",
        )
        == "id-ID"
    )


def test_resolve_progress_language_returns_none_value_when_country_differs() -> None:
    assert (
        resolve_progress_language(
            job_country="Indonesia",
            job_language_requirements=["Indonesian"],
            candidate_country_of_residence="Malaysia",
            candidate_native_languages="Indonesian",
        )
        == DEFAULT_PROGRESS_LANGUAGE
    )


def test_resolve_progress_language_returns_none_value_when_native_language_differs() -> None:
    assert (
        resolve_progress_language(
            job_country="Indonesia",
            job_language_requirements=["Indonesian"],
            candidate_country_of_residence="Indonesia",
            candidate_native_languages="Malay",
        )
        == DEFAULT_PROGRESS_LANGUAGE
    )


def test_resolve_progress_language_uses_first_matching_job_requirement_order() -> None:
    assert (
        resolve_progress_language(
            job_country="Malaysia",
            job_language_requirements=["Indonesian", "Malay"],
            candidate_country_of_residence="Malaysia",
            candidate_native_languages="Malay / English",
        )
        == "ms-MY"
    )


def test_normalize_progress_language_value_handles_legacy_list_values() -> None:
    assert normalize_progress_language_value(["泰语", "英语"]) == "泰语"
    assert normalize_progress_language_value([]) == DEFAULT_PROGRESS_LANGUAGE
    assert normalize_progress_language_value("") == DEFAULT_PROGRESS_LANGUAGE
