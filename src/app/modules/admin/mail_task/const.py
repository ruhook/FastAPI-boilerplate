from enum import StrEnum


class MailTaskStatus(StrEnum):
    PENDING = "pending"
    RENDERING = "rendering"
    SENDING = "sending"
    RETRYING = "retrying"
    SENT = "sent"
    FAILED = "failed"


MAIL_TASK_STATUS_CN_NAME_MAP = {
    MailTaskStatus.PENDING.value: "待处理",
    MailTaskStatus.RENDERING.value: "渲染中",
    MailTaskStatus.SENDING.value: "发送中",
    MailTaskStatus.RETRYING.value: "重试中",
    MailTaskStatus.SENT.value: "已发送",
    MailTaskStatus.FAILED.value: "发送失败",
}

MAIL_TASK_DATA_RENDER_CONTEXT_KEY = "render_context"
MAIL_TASK_DATA_RENDERED_CONTEXT_KEY = "rendered_context"
MAIL_TASK_DATA_RESEND_FROM_TASK_ID_KEY = "resend_from_task_id"
