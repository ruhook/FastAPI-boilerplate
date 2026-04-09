import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from ..app.core.db.database import async_engine, local_session
from ..app.core.security import get_password_hash
from ..app.modules.admin.admin_user.model import AdminUser
from ..app.modules.admin.admin_user.schema import AdminUserCreate
from ..app.modules.admin.admin_user.service import create_admin_account
from ..app.modules.admin.role.model import Role
from ..app.modules.admin.role.schema import RoleCreate
from ..app.modules.admin.role.service import create_role

DEFAULT_ROLE_NAME = "测试题判题人"
DEFAULT_ROLE_DESCRIPTION = "仅用于测试题回收阶段的判题操作。"
DEFAULT_PERMISSION = "测试题判题"
DEFAULT_NAME = "Judge Reviewer"
DEFAULT_EMAIL = "judge.reviewer@example.com"
DEFAULT_USERNAME = "judgereviewer"
DEFAULT_PASSWORD = "JudgeReview123!"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or update an admin assessment reviewer account.")
    parser.add_argument("--name", default=DEFAULT_NAME, help="Admin display name.")
    parser.add_argument("--email", default=DEFAULT_EMAIL, help="Admin email.")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="Admin username.")
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help="Admin password.")
    parser.add_argument("--role-name", default=DEFAULT_ROLE_NAME, help="Reviewer role name.")
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="Reset password when the admin account already exists.",
    )
    return parser.parse_args()


async def ensure_reviewer_role(*, role_name: str) -> Role:
    async with local_session() as session:
        result = await session.execute(select(Role).where(Role.name == role_name))
        role = result.scalar_one_or_none()
        if role is not None:
            permissions = list(role.permissions or [])
            if DEFAULT_PERMISSION not in permissions:
                permissions.append(DEFAULT_PERMISSION)
            role.permissions = permissions
            role.enabled = True
            if not role.description:
                role.description = DEFAULT_ROLE_DESCRIPTION
            role.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(role)
            return role

        payload = RoleCreate(
            name=role_name,
            description=DEFAULT_ROLE_DESCRIPTION,
            enabled=True,
            permissions=[DEFAULT_PERMISSION],
        )
        created = await create_role(payload, session)
        refreshed = await session.execute(select(Role).where(Role.id == created["id"]))
        role = refreshed.scalar_one()
        await session.commit()
        return role


async def ensure_reviewer_account(
    *,
    role_id: int,
    name: str,
    email: str,
    username: str,
    password: str,
    reset_password: bool,
) -> dict[str, str | int | None]:
    async with local_session() as session:
        result = await session.execute(
            select(AdminUser).where(
                AdminUser.is_deleted.is_(False),
                ((AdminUser.email == email) | (AdminUser.username == username)),
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.role_id = role_id
            existing.status = "enabled"
            existing.name = name
            existing.email = email
            existing.username = username
            if reset_password:
                existing.hashed_password = get_password_hash(password)
            existing.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(existing)
            return {
                "id": existing.id,
                "username": existing.username,
                "email": existing.email,
                "password": password if reset_password else None,
                "created": 0,
            }

        payload = AdminUserCreate(
            name=name,
            email=email,
            username=username,
            password=password,
            status="enabled",
            role_id=role_id,
        )
        created = await create_admin_account(payload, session)
        await session.commit()
        return {
            "id": int(created["id"]),
            "username": str(created["username"]),
            "email": str(created["email"]),
            "password": password,
            "created": 1,
        }


async def main() -> None:
    args = parse_args()
    role = await ensure_reviewer_role(role_name=args.role_name)
    account = await ensure_reviewer_account(
        role_id=role.id,
        name=args.name,
        email=args.email,
        username=args.username,
        password=args.password,
        reset_password=args.reset_password,
    )
    print(
        {
            "role": {
                "id": role.id,
                "name": role.name,
                "permissions": role.permissions,
            },
            "admin": account,
        }
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        asyncio.run(async_engine.dispose())
