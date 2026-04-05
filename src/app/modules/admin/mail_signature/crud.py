from fastcrud import FastCRUD

from .model import MailSignature
from .schema import (
    MailSignatureCreateInternal,
    MailSignatureDelete,
    MailSignatureRead,
    MailSignatureUpdate,
    MailSignatureUpdateInternal,
)

CRUDMailSignature = FastCRUD[
    MailSignature,
    MailSignatureCreateInternal,
    MailSignatureUpdate,
    MailSignatureUpdateInternal,
    MailSignatureDelete,
    MailSignatureRead,
]
crud_mail_signatures = CRUDMailSignature(MailSignature)

