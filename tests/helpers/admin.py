from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.security import get_password_hash
from src.app.modules.admin.admin_audit_log.model import AdminAuditLog
from src.app.modules.admin.admin_user.const import DEFAULT_ADMIN_PROFILE_IMAGE_URL
from src.app.modules.admin.admin_user.model import AdminUser
from src.app.modules.admin.role.model import Role


async def create_role(
    db_session: AsyncSession,
    *,
    name: str,
    permissions: list[str],
    description: str = "test role",
) -> Role:
    role = Role(
        name=name,
        description=description,
        enabled=True,
        permissions=permissions,
        data={},
    )
    db_session.add(role)
    await db_session.commit()
    await db_session.refresh(role)
    return role


async def create_admin_user(
    db_session: AsyncSession,
    *,
    role_id: int | None,
    name: str = "Test Admin",
    username_prefix: str = "tadmin",
    password: str = "AdminPass123!",
) -> tuple[AdminUser, str]:
    suffix = uuid4().hex[:8]
    admin = AdminUser(
        name=name,
        username=f"{username_prefix}{suffix}"[:20],
        email=f"{username_prefix}.{suffix}@example.com",
        hashed_password=get_password_hash(password),
        phone=None,
        note="test admin",
        status="enabled",
        profile_image_url=DEFAULT_ADMIN_PROFILE_IMAGE_URL,
        is_superuser=False,
        role_id=role_id,
        data={},
    )
    db_session.add(admin)
    await db_session.commit()
    await db_session.refresh(admin)
    return admin, password


async def login_admin_user(
    admin_client: AsyncClient,
    *,
    username_or_email: str,
    password: str,
) -> dict[str, str]:
    response = await admin_client.post(
        "/api/v1/auth/login",
        json={"username_or_email": username_or_email, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def fetch_admin_audit_logs(
    db_session: AsyncSession,
    *,
    admin_user_id: int,
) -> list[AdminAuditLog]:
    result = await db_session.execute(
        select(AdminAuditLog)
        .where(AdminAuditLog.admin_user_id == admin_user_id)
        .order_by(AdminAuditLog.id.asc())
    )
    return list(result.scalars().all())
