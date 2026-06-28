from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

DEFAULT_PROGRESS_LANGUAGE = "无"


@dataclass(frozen=True)
class ProgressLanguageRule:
    progress_language: str
    required_language: str
    residence_country: str


PROGRESS_LANGUAGE_RULES: tuple[ProgressLanguageRule, ...] = tuple(
    ProgressLanguageRule(progress_language=code, required_language=language, residence_country=country)
    for code, language, country in (
        ("en-UK", "English", "UK"),
        ("en-EU", "English", "EU"),
        ("id-ID", "Indonesian", "Indonesia"),
        ("vi-VN", "Vietnamese", "Vietnam"),
        ("th-TH", "Thai", "Thailand"),
        ("ms-MY", "Malay", "Malaysia"),
        ("ja-JP", "Japanese", "Japan"),
        ("ko-KR", "Korean", "South Korea"),
        ("fil-PH", "Filipino", "Philippines"),
        ("ar-ME", "Arabic", "Middle East"),
        ("ru-RU", "Russian", "Russia"),
        ("fr-FR", "French", "France"),
        ("de-DE", "German", "Germany"),
        ("pt-BR", "Portuguese", "Brazil"),
        ("es-MX", "Spanish", "Mexico"),
        ("it-IT", "Italian", "Italy"),
        ("af-ZA", "Afrikaans", "South Africa"),
        ("ar-MENA", "Arabic", "MENA"),
        ("ar-SA", "Arabic", "Saudi Arabia (KSA)"),
        ("ar-ZA", "Arabic", "South Africa"),
        ("az-AZ", "Azerbaijani", "Azerbaijan"),
        ("bg-BG", "Bulgarian", "Bulgaria"),
        ("bn-BD", "Bengali", "Bangladesh"),
        ("bn-IN", "Bengali", "West Bengal/ India"),
        ("ca-ES", "Catalan", "Catalonia/ Spain"),
        ("cantonese-HK", "Cantonese", "Hong Kong/ China"),
        ("ceb-PH", "Cebuano", "Central Philippines / Cebu"),
        ("cs-CZ", "Czech", "Czech Republic"),
        ("da-DK", "Danish", "Denmark"),
        ("el-GR", "Greek", "Greece"),
        ("en-AU", "English", "Australia"),
        ("en-CA", "English", "Canada"),
        ("en-HK", "English", "Hong Kong/ China"),
        ("en-SG", "English", "Singapore"),
        ("en-US", "English", "United States"),
        ("es-ES", "Spanish", "Spain"),
        ("et-EE", "Estonian", "Estonia"),
        ("fa-IR", "Persian", "Iran"),
        ("fi-FI", "Finnish", "Finland"),
        ("ga-IE", "Irish", "Ireland"),
        ("gu-IN", "Gujarati", "Gujarat/ India"),
        ("he-IL", "Hebrew", "Israel"),
        ("hi-IN", "Hindi", "India"),
        ("hr-HR", "Croatian", "Croatia"),
        ("hu-HU", "Hungarian", "Hungary"),
        ("is-IS", "Icelandic", "Iceland"),
        ("jv-ID", "Javanese", "Java/ Indonesia"),
        ("kk-KZ", "Kazakh", "Kazakhstan"),
        ("km-KH", "Khmer", "Cambodia"),
        ("kn-IN", "Kannada", "Karnataka/ India"),
        ("lt-LT", "Lithuanian", "Lithuania"),
        ("lv-LV", "Latvian", "Latvia"),
        ("mandarin-HK", "Mandarin", "Hong Kong/ China"),
        ("mandarin-SG", "Mandarin", "Singapore"),
        ("ml-IN", "Malayalam", "Kerala/ India"),
        ("mr-IN", "Marathi", "Maharashtra/ India"),
        ("my-MM", "Burmese", "Myanmar"),
        ("nl-BE", "Dutch", "Flanders/ Belgium"),
        ("nl-NL", "Dutch", "Netherlands"),
        ("no-NO", "Norwegian", "Norway"),
        ("pa-IN", "Punjabi", "Punjab/ India"),
        ("pa-PK", "Punjabi", "Punjab/ Pakistan"),
        ("pl-PL", "Polish", "Poland"),
        ("pt-PT", "Portuguese", "Portugal"),
        ("ro-RO", "Romanian", "Romania"),
        ("sk-SK", "Slovak", "Slovakia"),
        ("sl-SI", "Slovenian", "Slovenia"),
        ("sq-AL", "Albanian", "Albania"),
        ("sq-XK", "Albanian", "Kosovo"),
        ("sr-RS", "Serbian", "Serbia"),
        ("sv-SE", "Swedish", "Sweden"),
        ("sw-KE", "Swahili", "Kenya"),
        ("sw-TZ", "Swahili", "Tanzania"),
        ("ta-IN", "Tamil", "Tamil Nadu/ India"),
        ("ta-LK", "Tamil", "Sri Lanka"),
        ("te-IN", "Telugu", "Andhra Pradesh/ Telangana/ India"),
        ("tr-TR", "Turkish", "Türkiye"),
        ("uk-UA", "Ukrainian", "Ukraine"),
        ("ur-PK", "Urdu", "Pakistan"),
        ("uz-UZ", "Uzbek", "Uzbekistan"),
        ("zh-HK", "Traditional Chinese", "Hong Kong/ China"),
        ("zh-MO", "Traditional Chinese", "Macao/ China"),
        ("zh-TW", "Traditional Chinese", "Taiwan/ China"),
    )
)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_for_match(value: Any) -> str:
    return _normalize_text(value).casefold()


def normalize_progress_language_value(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            normalized = _normalize_text(item)
            if normalized:
                return normalized
        return DEFAULT_PROGRESS_LANGUAGE
    normalized = _normalize_text(value)
    return normalized or DEFAULT_PROGRESS_LANGUAGE


def normalize_language_requirements(value: Any) -> list[str]:
    if isinstance(value, list):
        return [normalized for item in value if (normalized := _normalize_text(item))]
    normalized = _normalize_text(value)
    return [normalized] if normalized else []


def split_candidate_languages(value: Any) -> set[str]:
    if isinstance(value, list):
        parts = [_normalize_text(item) for item in value]
    else:
        parts = re.split(r"[/,，、\n\r;；]+", _normalize_text(value))
    return {_normalize_for_match(part) for part in parts if _normalize_text(part)}


def resolve_progress_language(
    *,
    job_country: Any,
    job_language_requirements: Any,
    candidate_country_of_residence: Any,
    candidate_native_languages: Any,
) -> str:
    job_country_text = _normalize_for_match(job_country)
    candidate_country_text = _normalize_for_match(candidate_country_of_residence)
    if not job_country_text or job_country_text != candidate_country_text:
        return DEFAULT_PROGRESS_LANGUAGE

    candidate_language_set = split_candidate_languages(candidate_native_languages)
    if not candidate_language_set:
        return DEFAULT_PROGRESS_LANGUAGE

    rules_by_key = {
        (
            _normalize_for_match(rule.residence_country),
            _normalize_for_match(rule.required_language),
        ): rule.progress_language
        for rule in PROGRESS_LANGUAGE_RULES
    }
    for requirement in normalize_language_requirements(job_language_requirements):
        requirement_key = _normalize_for_match(requirement)
        if requirement_key not in candidate_language_set:
            continue
        matched = rules_by_key.get((job_country_text, requirement_key))
        if matched:
            return matched
    return DEFAULT_PROGRESS_LANGUAGE
