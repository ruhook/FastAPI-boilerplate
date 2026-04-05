MAIL_ACCOUNT_EMAIL_MAX_LENGTH = 255
MAIL_ACCOUNT_NOTE_MAX_LENGTH = 500
MAIL_ACCOUNT_PROVIDER_MAX_LENGTH = 32
MAIL_ACCOUNT_SECURITY_MODE_MAX_LENGTH = 16
MAIL_ACCOUNT_STATUS_MAX_LENGTH = 16

MAIL_ACCOUNT_PROVIDER_PRESETS: dict[str, dict[str, str | int]] = {
    "qq": {"label": "QQ 邮箱", "smtp_host": "smtp.qq.com", "smtp_port": 587, "security_mode": "starttls"},
    "163": {"label": "163 邮箱", "smtp_host": "smtp.163.com", "smtp_port": 465, "security_mode": "ssl"},
    "gmail": {"label": "Google Workspace / Gmail", "smtp_host": "smtp.gmail.com", "smtp_port": 587, "security_mode": "starttls"},
    "m365": {"label": "Microsoft 365 / Outlook", "smtp_host": "smtp.office365.com", "smtp_port": 587, "security_mode": "starttls"},
}

MAIL_ACCOUNT_PROVIDERS = set(MAIL_ACCOUNT_PROVIDER_PRESETS)
MAIL_ACCOUNT_SECURITY_MODES = {"ssl", "starttls", "none"}
MAIL_ACCOUNT_STATUSES = {"enabled", "pending", "disabled"}

