from src.app.admin.local_admin_bootstrap import (
    LOCAL_ADMIN_EMAIL,
    LOCAL_ADMIN_NAME,
    LOCAL_ADMIN_PASSWORD,
    LOCAL_ADMIN_USERNAME,
    build_local_admin_values,
    should_ensure_local_admin,
)
from src.app.core.config import EnvironmentOption


def test_should_ensure_local_admin_only_in_local_environment() -> None:
    assert should_ensure_local_admin(EnvironmentOption.LOCAL) is True
    assert should_ensure_local_admin(EnvironmentOption.STAGING) is False
    assert should_ensure_local_admin(EnvironmentOption.PRODUCTION) is False


def test_build_local_admin_values_uses_expected_dev_credentials() -> None:
    values = build_local_admin_values()

    assert values["name"] == LOCAL_ADMIN_NAME
    assert values["username"] == LOCAL_ADMIN_USERNAME
    assert values["email"] == LOCAL_ADMIN_EMAIL
    assert values["password"] == LOCAL_ADMIN_PASSWORD
    assert values["status"] == "enabled"
    assert values["is_superuser"] is True
    assert values["is_deleted"] is False
    assert values["role_id"] is None


if __name__ == "__main__":
    test_should_ensure_local_admin_only_in_local_environment()
    test_build_local_admin_values_uses_expected_dev_credentials()
