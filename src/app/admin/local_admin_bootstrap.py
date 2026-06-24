import logging
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import EnvironmentOption, Settings
from ..core.db.database import local_session
from ..core.security import get_password_hash
from ..modules.admin.admin_user.const import DEFAULT_ADMIN_PROFILE_IMAGE_URL
from ..modules.admin.admin_user.model import AdminUser
from ..modules.admin.role.model import Role  # noqa: F401

logger = logging.getLogger(__name__)

LOCAL_ADMIN_NAME = "Haokang Import"
LOCAL_ADMIN_USERNAME = "HaokangImport"
LOCAL_ADMIN_EMAIL = "haokang-import-admin@example.com"
LOCAL_ADMIN_PASSWORD = "HaokangImport123!"


def should_ensure_local_admin(environment: EnvironmentOption | str) -> bool:
    return environment == EnvironmentOption.LOCAL or environment == EnvironmentOption.LOCAL.value


def build_local_admin_values() -> dict[str, Any]:
    return {
        "name": LOCAL_ADMIN_NAME,
        "username": LOCAL_ADMIN_USERNAME,
        "email": LOCAL_ADMIN_EMAIL,
        "password": LOCAL_ADMIN_PASSWORD,
        "status": "enabled",
        "profile_image_url": DEFAULT_ADMIN_PROFILE_IMAGE_URL,
        "is_superuser": True,
        "is_deleted": False,
        "role_id": None,
        "data": {},
    }


async def ensure_local_admin_user(session: AsyncSession) -> AdminUser:
    values = build_local_admin_values()
    result = await session.execute(
        select(AdminUser)
        .where(
            or_(
                AdminUser.username == LOCAL_ADMIN_USERNAME,
                AdminUser.username == LOCAL_ADMIN_USERNAME.lower(),
                AdminUser.email == LOCAL_ADMIN_EMAIL,
            )
        )
        .order_by(AdminUser.is_deleted.asc(), AdminUser.id.asc())
    )
    admin = result.scalars().first()

    if admin is None:
        admin = AdminUser(
            name=values["name"],
            username=values["username"],
            email=values["email"],
            hashed_password=get_password_hash(values["password"]),
            phone=None,
            note="Local development superuser.",
            status=values["status"],
            profile_image_url=values["profile_image_url"],
            is_superuser=values["is_superuser"],
            role_id=values["role_id"],
            data=values["data"],
        )
        session.add(admin)
        await session.flush()
        logger.info("Created local development admin account: %s", LOCAL_ADMIN_USERNAME)
        return admin

    admin.name = values["name"]
    admin.username = values["username"]
    admin.email = values["email"]
    admin.hashed_password = get_password_hash(values["password"])
    admin.status = values["status"]
    admin.profile_image_url = admin.profile_image_url or values["profile_image_url"]
    admin.is_superuser = values["is_superuser"]
    admin.is_deleted = values["is_deleted"]
    admin.deleted_at = None
    admin.role_id = values["role_id"]
    admin.data = dict(admin.data or {})
    logger.info("Ensured local development admin account: %s", LOCAL_ADMIN_USERNAME)
    return admin


async def ensure_local_admin_for_settings(settings: Settings) -> None:
    if not should_ensure_local_admin(settings.ENVIRONMENT):
        return

    async with local_session() as session:
        await ensure_local_admin_user(session)
        await session.commit()
