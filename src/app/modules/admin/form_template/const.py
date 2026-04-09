from enum import StrEnum


class FormFieldType(StrEnum):
    TEXT = "text"
    EMAIL = "email"
    NUMBER = "number"
    SELECT = "select"
    MULTISELECT = "multiselect"
    FILE = "file"
    BOOLEAN = "boolean"


class FormFieldGroup(StrEnum):
    BASIC = "basic"
    WORK = "work"
    OTHER = "other"
