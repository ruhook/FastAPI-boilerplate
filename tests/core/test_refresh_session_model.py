import pytest

from src.app.modules.admin.admin_user.model import AdminUser
from src.app.modules.auth_refresh_session.model import AuthRefreshSession
from src.app.modules.user.model import User

pytestmark = pytest.mark.no_database_cleanup


def test_accounts_have_non_nullable_token_versions() -> None:
    for model in (User, AdminUser):
        column = model.__table__.c.token_version
        assert column.nullable is False
        assert column.default is not None
        assert column.default.arg == 0


def test_refresh_session_stores_hash_and_rotation_metadata_only() -> None:
    columns = AuthRefreshSession.__table__.c

    assert "token" not in columns
    assert columns.token_hash.unique is True
    assert columns.token_hash.type.length == 64
    assert columns.portal.nullable is False
    assert columns.account_id.nullable is False
    assert columns.family_id.nullable is False
    assert columns.parent_session_id.nullable is True
    assert columns.expires_at.nullable is False
    assert columns.revoked_at.nullable is True
    assert columns.rotation_at.nullable is True
