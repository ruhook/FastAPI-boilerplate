from fastcrud import FastCRUD

from .model import MailTemplateCategory
from .schema import (
    MailTemplateCategoryCreateInternal,
    MailTemplateCategoryDelete,
    MailTemplateCategoryRead,
    MailTemplateCategoryUpdate,
    MailTemplateCategoryUpdateInternal,
)

CRUDMailTemplateCategory = FastCRUD[
    MailTemplateCategory,
    MailTemplateCategoryCreateInternal,
    MailTemplateCategoryUpdate,
    MailTemplateCategoryUpdateInternal,
    MailTemplateCategoryDelete,
    MailTemplateCategoryRead,
]
crud_mail_template_categories = CRUDMailTemplateCategory(MailTemplateCategory)

