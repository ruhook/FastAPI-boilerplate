from fastcrud import FastCRUD

from .model import MailAccount
from .schema import (
    MailAccountCreateInternal,
    MailAccountDelete,
    MailAccountRead,
    MailAccountUpdate,
    MailAccountUpdateInternal,
)

CRUDMailAccount = FastCRUD[
    MailAccount,
    MailAccountCreateInternal,
    MailAccountUpdate,
    MailAccountUpdateInternal,
    MailAccountDelete,
    MailAccountRead,
]
crud_mail_accounts = CRUDMailAccount(MailAccount)

