import pytest

from src.app.modules.candidate_field.const import (
    CANDIDATE_FIELD_CN_NAME_MAP,
    CANDIDATE_FIELD_DICTIONARY_KEY_MAP,
    CANDIDATE_FIELD_SELECT_OPTIONS_EN_MAP,
    CandidateFieldKey,
    build_candidate_field_catalog_options,
)
from src.scripts.seed_candidate_base_form_template import (
    DICTIONARY_DEFINITIONS,
    FORM_TEMPLATE_FIELDS,
)

pytestmark = pytest.mark.no_database_cleanup


EXPECTED_ENGLISH_PROFICIENCY_OPTIONS = [
    {"label": "Native Speaker", "admin_label": "母语水平", "value": "native_speaker"},
    {
        "label": "Fully professional proficiency (can work independently in English)",
        "admin_label": "完全职业熟练（可独立用英语工作）",
        "value": "fully_professional_proficiency",
    },
    {
        "label": "Intermediate level (comfortable in daily writing and communication only)",
        "admin_label": "中级（仅能完成日常写作和沟通）",
        "value": "intermediate_level",
    },
    {"label": "Basic level", "admin_label": "基础", "value": "basic_level"},
    {"label": "No English", "admin_label": "无英语能力", "value": "no_english"},
]


def test_english_proficiency_is_registered_as_candidate_field() -> None:
    assert CandidateFieldKey.ENGLISH_PROFICIENCY.value == "english_proficiency"
    assert CANDIDATE_FIELD_CN_NAME_MAP[CandidateFieldKey.ENGLISH_PROFICIENCY] == "英语水平"
    assert (
        CANDIDATE_FIELD_DICTIONARY_KEY_MAP[CandidateFieldKey.ENGLISH_PROFICIENCY]
        == "candidate_english_proficiency"
    )
    assert {"label": "英语水平", "value": "english_proficiency"} in build_candidate_field_catalog_options()
    assert CANDIDATE_FIELD_SELECT_OPTIONS_EN_MAP["english_proficiency"] == [
        {"label": option["label"], "value": option["value"]}
        for option in EXPECTED_ENGLISH_PROFICIENCY_OPTIONS
    ]


def test_base_candidate_form_template_contains_english_proficiency_select() -> None:
    field = next(
        item for item in FORM_TEMPLATE_FIELDS if item["key"] == "english_proficiency"
    )

    assert field == {
        "key": "english_proficiency",
        "label": "What is your English proficiency level?",
        "type": "select",
        "required": True,
        "group": "basic",
        "canFilter": True,
        "dictionary_key": "candidate_english_proficiency",
    }


def test_base_candidate_form_seed_contains_english_proficiency_dictionary() -> None:
    dictionary = next(
        item
        for item in DICTIONARY_DEFINITIONS
        if item["key"] == "candidate_english_proficiency"
    )

    assert dictionary == {
        "key": "candidate_english_proficiency",
        "label": "候选人英语水平",
        "options": EXPECTED_ENGLISH_PROFICIENCY_OPTIONS,
    }
