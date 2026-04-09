from enum import StrEnum


class MailAccountProvider(StrEnum):
    QQ = "qq"


class MailAccountSecurityMode(StrEnum):
    SSL = "ssl"
    STARTTLS = "starttls"
    NONE = "none"


class MailAccountStatus(StrEnum):
    ENABLED = "enabled"
    PENDING = "pending"
    DISABLED = "disabled"

MAIL_ACCOUNT_PROVIDER_PRESETS: dict[str, dict[str, str | int]] = {
    MailAccountProvider.QQ.value: {
        "label": "QQ 邮箱",
        "smtp_host": "smtp.qq.com",
        "smtp_port": 587,
        "security_mode": MailAccountSecurityMode.STARTTLS.value,
    },
}
