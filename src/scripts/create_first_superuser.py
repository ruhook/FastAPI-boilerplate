import asyncio
import argparse
import getpass
import logging

from sqlalchemy import insert, select

from ..app.core.db.database import AsyncSession, async_engine, local_session
from ..app.core.security import get_password_hash
from ..app.modules.admin.admin_user.model import AdminUser
from ..app.modules.admin.admin_user.schema import AdminUserCreate

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_SUPERUSER = {
    "name": "Admin",
    "email": "admin@admin.com",
    "username": "admin",
    "password": "12345678",
}


def prompt_value(label: str, default: str) -> str:
    value = input(f"{label} [{default}]: ").strip()
    return value or default


def prompt_superuser_data() -> AdminUserCreate:
    logger.info("Creating the first admin superuser interactively.")

    while True:
        name = prompt_value("Name", DEFAULT_SUPERUSER["name"])
        email = prompt_value("Email", DEFAULT_SUPERUSER["email"])
        username = prompt_value("Username", DEFAULT_SUPERUSER["username"])
        password = getpass.getpass("Password: ").strip()
        confirm_password = getpass.getpass("Confirm password: ").strip()

        if password != confirm_password:
            logger.error("Passwords do not match. Please try again.")
            continue

        try:
            return AdminUserCreate(
                name=name,
                email=email,
                username=username,
                password=password,
                status="enabled",
            )
        except Exception as exc:
            logger.error(str(exc))


def build_default_superuser_data() -> AdminUserCreate:
    logger.info("Creating the first admin superuser with default credentials.")
    return AdminUserCreate(
        name=DEFAULT_SUPERUSER["name"],
        email=DEFAULT_SUPERUSER["email"],
        username=DEFAULT_SUPERUSER["username"],
        password=DEFAULT_SUPERUSER["password"],
        status="enabled",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create the first admin superuser.")
    parser.add_argument(
        "--default",
        action="store_true",
        help="Create the default admin account directly without interactive prompts.",
    )
    return parser.parse_args()


async def create_first_user(session: AsyncSession, *, use_default: bool = False) -> None:
    superuser = build_default_superuser_data() if use_default else prompt_superuser_data()
    existing = await session.execute(select(AdminUser).filter_by(email=superuser.email))
    admin_user = existing.scalar_one_or_none()

    if admin_user is not None:
        logger.info("Admin user already exists.")
        return

    values = superuser.model_dump(exclude={"password"})
    await session.execute(
        insert(AdminUser).values(
            name=values["name"],
            email=values["email"],
            username=values["username"],
            hashed_password=get_password_hash(superuser.password or ""),
            phone=None,
            note=None,
            status="enabled",
            profile_image_url="https://profileimageurl.com",
            is_superuser=True,
            role_id=None,
            data={},
        )
    )
    await session.commit()
    logger.info(f"Admin user {values['username']} created successfully.")


async def main() -> None:
    args = parse_args()
    try:
        async with local_session() as session:
            await create_first_user(session, use_default=args.default)
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
