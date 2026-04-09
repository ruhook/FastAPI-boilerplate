from .const import CANDIDATE_FIELD_CN_NAME_MAP, CANDIDATE_FIELD_SELECT_OPTIONS_EN_MAP


def list_candidate_field_catalog() -> list[dict[str, str]]:
    return [
        {"key": field_key.value, "label": label}
        for field_key, label in CANDIDATE_FIELD_CN_NAME_MAP.items()
    ]


def hydrate_candidate_field_options(form_fields: list[dict[str, object]]) -> list[dict[str, object]]:
    hydrated_fields: list[dict[str, object]] = []
    for raw_field in form_fields:
        field = dict(raw_field)
        field_type = str(field.get("type") or "").strip().lower()
        field_key = str(field.get("key") or "").strip()
        if field_type in {"select", "single_select", "dictionary"} and not field.get("options"):
            options = CANDIDATE_FIELD_SELECT_OPTIONS_EN_MAP.get(field_key)
            if options:
                field["options"] = options
        hydrated_fields.append(field)
    return hydrated_fields
