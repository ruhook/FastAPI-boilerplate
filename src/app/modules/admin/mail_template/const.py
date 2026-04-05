from typing import Final

MAIL_TEMPLATE_NAME_MAX_LENGTH = 120
MAIL_TEMPLATE_SUBJECT_MAX_LENGTH = 500

MAIL_VARIABLE_CATALOG: Final[list[dict[str, str]]] = [
    {"key": "candidate_name", "label": "候选人姓名"},
    {"key": "job_title", "label": "岗位名称"},
    {"key": "assessment_link", "label": "测试题链接"},
    {"key": "due_date", "label": "截止日期"},
    {"key": "company_name", "label": "公司名称"},
    {"key": "sender_name", "label": "发件人姓名"},
]

