from fastcrud import FastCRUD

from .model import MailTemplate
from .schema import (
    MailTemplateCreateInternal,
    MailTemplateDelete,
    MailTemplateRead,
    MailTemplateUpdate,
    MailTemplateUpdateInternal,
)

CRUDMailTemplate = FastCRUD[
    MailTemplate,
    MailTemplateCreateInternal,
    MailTemplateUpdate,
    MailTemplateUpdateInternal,
    MailTemplateDelete,
    MailTemplateRead,
]
crud_mail_templates = CRUDMailTemplate(MailTemplate)

