from enum import StrEnum


class AdminAccountStatus(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


DEFAULT_ADMIN_PROFILE_IMAGE_URL = "https://profileimageurl.com"
