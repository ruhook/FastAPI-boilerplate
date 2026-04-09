from enum import StrEnum


class AdminAuditLogActionType(StrEnum):
    ADMIN_LOGIN = "admin_login"
    ADMIN_PASSWORD_CHANGED = "admin_password_changed"
    DICTIONARY_CREATED = "dictionary_created"
    DICTIONARY_UPDATED = "dictionary_updated"
    DICTIONARY_DELETED = "dictionary_deleted"
    FORM_TEMPLATE_CREATED = "form_template_created"
    FORM_TEMPLATE_UPDATED = "form_template_updated"
    FORM_TEMPLATE_DELETED = "form_template_deleted"
    MAIL_ACCOUNT_CREATED = "mail_account_created"
    MAIL_ACCOUNT_UPDATED = "mail_account_updated"
    MAIL_ACCOUNT_DELETED = "mail_account_deleted"
    MAIL_TEMPLATE_CATEGORY_CREATED = "mail_template_category_created"
    MAIL_TEMPLATE_CATEGORY_UPDATED = "mail_template_category_updated"
    MAIL_TEMPLATE_CATEGORY_DELETED = "mail_template_category_deleted"
    MAIL_TEMPLATE_CREATED = "mail_template_created"
    MAIL_TEMPLATE_UPDATED = "mail_template_updated"
    MAIL_TEMPLATE_DELETED = "mail_template_deleted"
    MAIL_SIGNATURE_CREATED = "mail_signature_created"
    MAIL_SIGNATURE_UPDATED = "mail_signature_updated"
    MAIL_SIGNATURE_DELETED = "mail_signature_deleted"


class AdminAuditLogTargetType(StrEnum):
    ADMIN_AUTH = "admin_auth"
    DICTIONARY = "dictionary"
    FORM_TEMPLATE = "form_template"
    MAIL_ACCOUNT = "mail_account"
    MAIL_TEMPLATE_CATEGORY = "mail_template_category"
    MAIL_TEMPLATE = "mail_template"
    MAIL_SIGNATURE = "mail_signature"
