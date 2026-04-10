from enum import StrEnum


class JobStatus(StrEnum):
    OPEN = "在招"
    PAUSED = "暂停"
    CLOSED = "关闭"


class JobWorkMode(StrEnum):
    REMOTE = "Remote"
    ONSITE = "Onsite"

JOB_DATA_COLLABORATORS_KEY = "collaborators"
JOB_DATA_HIGHLIGHTS_KEY = "highlights"
JOB_DATA_FORM_FIELDS_KEY = "form_fields"
JOB_DATA_AUTOMATION_RULES_KEY = "automation_rules"
JOB_DATA_SCREENING_RULES_KEY = "screening_rules"
JOB_DATA_PUBLISH_CHECKLIST_KEY = "publish_checklist"
JOB_DATA_APPLICATION_SUMMARY_KEY = "application_summary"
JOB_DATA_REJECTION_MAIL_CONFIG_KEY = "rejection_mail_config"
