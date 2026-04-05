from fastcrud import FastCRUD

from .model import MailTask
from .schema import MailTaskCreateInternal, MailTaskRead, MailTaskUpdateInternal

CRUDMailTask = FastCRUD[
    MailTask,
    MailTaskCreateInternal,
    MailTaskUpdateInternal,
    MailTaskUpdateInternal,
    MailTaskUpdateInternal,
    MailTaskRead,
]
crud_mail_tasks = CRUDMailTask(MailTask)
