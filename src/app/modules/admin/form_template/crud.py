from fastcrud import FastCRUD

from .model import AdminFormTemplate
from .schema import (
    FormTemplateCreateInternal,
    FormTemplateDelete,
    FormTemplateRead,
    FormTemplateUpdate,
    FormTemplateUpdateInternal,
)

CRUDAdminFormTemplate = FastCRUD[
    AdminFormTemplate,
    FormTemplateCreateInternal,
    FormTemplateUpdate,
    FormTemplateUpdateInternal,
    FormTemplateDelete,
    FormTemplateRead,
]
crud_form_templates = CRUDAdminFormTemplate(AdminFormTemplate)

