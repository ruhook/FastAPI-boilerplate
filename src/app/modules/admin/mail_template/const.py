from typing import Final

MAIL_VARIABLE_CATALOG: Final[list[dict[str, str]]] = [
    {"key": "candidate_name", "label": "候选人姓名"},
    {"key": "candidate_email", "label": "候选人邮箱"},
    {"key": "job_title", "label": "岗位名称"},
    {"key": "assessment_link", "label": "测试题链接"},
    {"key": "due_date", "label": "截止日期"},
    {"key": "company_name", "label": "公司名称"},
    {"key": "sender_name", "label": "发件人姓名"},
    {"key": "sender_email", "label": "发件人邮箱"},
    {"key": "template_name", "label": "邮件模板名称"},
    {"key": "signature_name", "label": "签名模板名称"},
    {"key": "signature_full_name", "label": "签名姓名"},
    {"key": "signature_job_title", "label": "签名职位"},
    {"key": "signature_company_name", "label": "签名公司"},
    {"key": "signature_primary_email", "label": "签名主邮箱"},
    {"key": "signature_secondary_email", "label": "签名备用邮箱"},
    {"key": "signature_website", "label": "签名官网"},
    {"key": "signature_linkedin_label", "label": "签名 LinkedIn 标题"},
    {"key": "signature_linkedin_url", "label": "签名 LinkedIn 链接"},
    {"key": "signature_address", "label": "签名地址"},
]
