import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.exceptions.http_exceptions import DuplicateValueException, ForbiddenException, NotFoundException, UnauthorizedException
from ....core.security import (
    ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES,
    ADMIN_REFRESH_TOKEN_EXPIRE_DAYS,
    authenticate_admin_user,
    create_access_token,
    create_refresh_token,
    get_password_hash,
    verify_password,
)
from ..admin_audit_log.const import AdminAuditLogActionType, AdminAuditLogTargetType
from ..admin_audit_log.service import create_admin_audit_log
from ..role.crud import crud_roles
from ..role.model import Role
from ..role.const import validate_permissions
from ..role.schema import RoleRead
from .const import DEFAULT_ADMIN_PROFILE_IMAGE_URL
from .crud import crud_admin_users
from .model import AdminUser
from .schema import AdminChangePasswordRequest, AdminLoginRequest, AdminToken, AdminUserAuth, AdminUserCreate, AdminUserCreateInternal, AdminUserCreateResponse, AdminUserDBRead, AdminUserRead, AdminUserUpdate, generate_temporary_password


def build_admin_user_create_values(payload: AdminUserCreate, username: str, hashed_password: str) -> AdminUserCreateInternal:
    return AdminUserCreateInternal(
        name=payload.name,
        username=username,
        email=payload.email,
        hashed_password=hashed_password,
        phone=payload.phone,
        note=payload.note,
        status=payload.status,
        profile_image_url=payload.profile_image_url or DEFAULT_ADMIN_PROFILE_IMAGE_URL,
        is_superuser=False,
        role_id=payload.role_id,
        data={},
    )


def build_admin_user_update_values(payload: AdminUserUpdate, existing_data: dict[str, Any] | None = None) -> dict[str, Any]:
    update_data = payload.model_dump(exclude_none=True, exclude={"password"})
    update_data["data"] = dict(existing_data or {})
    return update_data


def build_deleted_admin_identity(account_id: int) -> tuple[str, str]:
    archived_username = f"deleted{account_id}"
    archived_email = f"deleted+{account_id}@local.invalid"
    return archived_username[:20], archived_email[:100]


def serialize_admin_user(
    account: AdminUser,
    role_name: str | None = None,
    role_id_override: int | None | object = ...,
) -> dict[str, Any]:
    resolved_role_name = "超级管理员" if account.is_superuser else role_name
    resolved_role_id = account.role_id if role_id_override is ... else role_id_override
    return AdminUserRead(
        id=account.id,
        name=account.name,
        username=account.username,
        email=account.email,
        phone=account.phone,
        note=account.note,
        status=account.status,
        profile_image_url=account.profile_image_url,
        role_id=resolved_role_id,
        role_name=resolved_role_name,
        is_superuser=account.is_superuser,
        last_login_at=account.last_login_at,
        created_at=account.created_at,
        updated_at=account.updated_at,
    ).model_dump()


async def get_account_with_role(db: AsyncSession, account_id: int) -> tuple[AdminUser, str | None, int | None] | None:
    result = await db.execute(select(AdminUser).where(AdminUser.id == account_id, AdminUser.is_deleted.is_(False)))
    account = result.scalar_one_or_none()
    if account is None:
        return None
    role_name, permissions = await resolve_admin_role_assignment(db=db, admin_user_id=account.id, role_id=account.role_id)
    effective_role_id = account.role_id if role_name is not None or permissions else None
    return account, role_name, effective_role_id


async def resolve_admin_role_assignment(
    db: AsyncSession,
    admin_user_id: int,
    role_id: int | None,
) -> tuple[str | None, list[str]]:
    if role_id is None:
        return None, []

    result = await db.execute(select(Role).where(Role.id == role_id))
    role = result.scalar_one_or_none()
    if role is None:
        await crud_admin_users.update(
            db=db,
            object={"role_id": None, "updated_at": datetime.now(UTC)},
            id=admin_user_id,
        )
        return None, []

    try:
        permissions = validate_permissions(role.permissions or [])
    except ValueError:
        await crud_admin_users.update(
            db=db,
            object={"role_id": None, "updated_at": datetime.now(UTC)},
            id=admin_user_id,
        )
        return None, []

    if not role.enabled:
        return None, []

    return role.name, permissions


async def ensure_role_exists(db: AsyncSession, role_id: int | None) -> RoleRead | None:
    if role_id is None:
        return None
    role = await crud_roles.get(db=db, id=role_id, schema_to_select=RoleRead)
    if role is None:
        raise NotFoundException("Role not found.")
    return role


def slugify_username(source: str) -> str:
    username = re.sub(r"[^a-z0-9]", "", source.lower())
    return username or "admin"


async def build_unique_username(db: AsyncSession, preferred: str) -> str:
    base = slugify_username(preferred)
    candidate = base
    index = 1
    while await crud_admin_users.exists(db=db, username=candidate, is_deleted=False):
        candidate = f"{base}{index}"
        index += 1
    return candidate


async def query_admin_accounts(
    db: AsyncSession,
    keyword: str | None = None,
    required_permission: str | None = None,
) -> list[dict[str, Any]]:
    stmt: Select[Any] = (
        select(AdminUser)
        .where(AdminUser.is_deleted.is_(False))
        .order_by(AdminUser.created_at.desc())
    )
    if keyword:
        keyword_value = f"%{keyword.strip()}%"
        stmt = stmt.where(
            or_(
                AdminUser.name.like(keyword_value),
                AdminUser.email.like(keyword_value),
                AdminUser.username.like(keyword_value),
                AdminUser.phone.like(keyword_value),
            )
        )
    result = await db.execute(stmt)
    accounts = result.scalars().all()
    serialized_accounts: list[dict[str, Any]] = []
    for account in accounts:
        role_name, permissions = await resolve_admin_role_assignment(
            db=db,
            admin_user_id=account.id,
            role_id=account.role_id,
        )
        if required_permission and not account.is_superuser and required_permission not in permissions:
            continue
        effective_role_id = account.role_id if role_name is not None or permissions else None
        serialized_accounts.append(serialize_admin_user(account, role_name, effective_role_id))
    return serialized_accounts


async def create_admin_account(payload: AdminUserCreate, db: AsyncSession) -> dict[str, Any]:
    if await crud_admin_users.exists(db=db, email=payload.email, is_deleted=False):
        raise DuplicateValueException("Email is already registered.")
    await ensure_role_exists(db, payload.role_id)
    username = payload.username or await build_unique_username(db, payload.email.split("@", 1)[0])
    if await crud_admin_users.exists(db=db, username=username, is_deleted=False):
        raise DuplicateValueException("Username not available.")

    password = payload.password or generate_temporary_password()
    internal = build_admin_user_create_values(payload, username, get_password_hash(password))
    created = await crud_admin_users.create(
        db=db,
        object=internal,
        schema_to_select=AdminUserDBRead,
        return_as_model=True,
    )
    created_id = created.id
    account_with_role = await get_account_with_role(db, created_id)
    if account_with_role is None:
        raise NotFoundException("Failed to create admin account.")
    account, role_name, effective_role_id = account_with_role
    response = serialize_admin_user(account, role_name, effective_role_id)
    response["temporary_password"] = None if payload.password else password
    return response


async def update_admin_account(
    account_id: int,
    payload: AdminUserUpdate,
    current_admin: dict[str, Any],
    db: AsyncSession,
) -> dict[str, Any]:
    account_with_role = await get_account_with_role(db, account_id)
    if account_with_role is None:
        raise NotFoundException("Admin account not found.")
    account, _, _ = account_with_role

    if (
        payload.email
        and payload.email != account.email
        and await crud_admin_users.exists(db=db, email=payload.email, is_deleted=False)
    ):
        raise DuplicateValueException("Email is already registered.")
    if (
        payload.username
        and payload.username != account.username
        and await crud_admin_users.exists(db=db, username=payload.username, is_deleted=False)
    ):
        raise DuplicateValueException("Username not available.")
    await ensure_role_exists(db, payload.role_id)

    update_data = build_admin_user_update_values(payload, existing_data=account.data)
    if payload.password:
        update_data["hashed_password"] = get_password_hash(payload.password)
    if account.is_superuser and "status" in update_data:
        raise ForbiddenException("Superuser account status cannot be changed.")
    if current_admin["id"] == account_id and update_data.get("status") == "disabled" and not current_admin["is_superuser"]:
        raise ForbiddenException("You cannot disable your own current admin account.")
    await crud_admin_users.update(
        db=db,
        object={**update_data, "updated_at": datetime.now(UTC)},
        id=account_id,
    )
    refreshed = await get_account_with_role(db, account_id)
    if refreshed is None:
        raise NotFoundException("Admin account not found.")
    refreshed_account, role_name, effective_role_id = refreshed
    return serialize_admin_user(refreshed_account, role_name, effective_role_id)


async def delete_admin_account(account_id: int, current_admin: dict[str, Any], db: AsyncSession) -> dict[str, str]:
    if current_admin["id"] == account_id:
        raise ForbiddenException("You cannot delete your own current admin account.")
    account = await crud_admin_users.get(db=db, id=account_id, is_deleted=False)
    if account is None:
        raise NotFoundException("Admin account not found.")
    if account["is_superuser"]:
        raise ForbiddenException("Superuser account cannot be deleted.")
    archived_username, archived_email = build_deleted_admin_identity(account_id)
    archived_data = dict(account.get("data") or {})
    archived_data.update(
        {
            "archived_username": account["username"],
            "archived_email": account["email"],
        }
    )
    await crud_admin_users.update(
        db=db,
        object={
            "username": archived_username,
            "email": archived_email,
            "role_id": None,
            "data": archived_data,
            "is_deleted": True,
            "deleted_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
        id=account_id,
    )
    return {"message": "Admin account deleted."}


async def build_admin_auth_user(admin_user: dict[str, Any], db: AsyncSession, all_permissions: list[str]) -> AdminUserAuth:
    permissions: list[str] = all_permissions if admin_user["is_superuser"] else []
    role_name: str | None = "超级管理员" if admin_user["is_superuser"] else None
    if not admin_user["is_superuser"] and admin_user["role_id"] is not None:
        role_name, permissions = await resolve_admin_role_assignment(
            db=db,
            admin_user_id=admin_user["id"],
            role_id=admin_user["role_id"],
        )

    return AdminUserAuth(
        id=admin_user["id"],
        name=admin_user["name"],
        username=admin_user["username"],
        email=admin_user["email"],
        phone=admin_user.get("phone"),
        note=admin_user.get("note"),
        status=admin_user["status"],
        profile_image_url=admin_user["profile_image_url"],
        role_id=admin_user.get("role_id"),
        role_name=role_name,
        is_superuser=admin_user["is_superuser"],
        last_login_at=admin_user.get("last_login_at"),
        created_at=admin_user["created_at"],
        updated_at=admin_user.get("updated_at"),
        permissions=permissions,
    )


async def issue_admin_tokens(admin_user: dict[str, Any], db: AsyncSession, all_permissions: list[str]) -> AdminToken:
    access_expires = timedelta(minutes=ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES)
    refresh_expires = timedelta(days=ADMIN_REFRESH_TOKEN_EXPIRE_DAYS)
    token_payload = {"sub": admin_user["username"], "portal": "admin"}
    access_token = await create_access_token(data=token_payload, expires_delta=access_expires)
    refresh_token = await create_refresh_token(data=token_payload, expires_delta=refresh_expires)
    user = await build_admin_auth_user(admin_user, db, all_permissions)
    return AdminToken(
        access_token=access_token,
        refresh_token=refresh_token,
        access_token_expires_in=ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        refresh_token_expires_in=ADMIN_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        user=user,
    )


async def login_admin_user(
    payload: AdminLoginRequest,
    db: AsyncSession,
    all_permissions: list[str],
) -> AdminToken:
    admin_user = await authenticate_admin_user(payload.username_or_email, payload.password, db)
    if not admin_user:
        raise UnauthorizedException("Wrong username, email or password.")
    await crud_admin_users.update(
        db=db,
        object={"last_login_at": datetime.now(UTC), "updated_at": datetime.now(UTC)},
        id=admin_user["id"],
    )
    refreshed_user = await crud_admin_users.get(db=db, id=admin_user["id"], is_deleted=False)
    if refreshed_user is None:
        raise UnauthorizedException("Admin not authenticated.")
    await create_admin_audit_log(
        db=db,
        admin_user_id=refreshed_user["id"],
        action_type=AdminAuditLogActionType.ADMIN_LOGIN.value,
        target_type=AdminAuditLogTargetType.ADMIN_AUTH.value,
        target_id=refreshed_user["id"],
        data={"username": refreshed_user["username"]},
    )
    return await issue_admin_tokens(refreshed_user, db, all_permissions)


async def refresh_admin_user_tokens(
    admin_user: dict[str, Any],
    refresh_token: str,
    db: AsyncSession,
    all_permissions: list[str],
) -> AdminToken:
    if admin_user is None or admin_user["status"] != "enabled":
        raise UnauthorizedException("Admin not authenticated.")
    _ = refresh_token
    return await issue_admin_tokens(admin_user, db, all_permissions)


async def change_current_admin_password(
    payload: AdminChangePasswordRequest,
    current_admin: dict[str, Any],
    db: AsyncSession,
) -> dict[str, str]:
    account = await crud_admin_users.get(db=db, id=current_admin["id"], is_deleted=False)
    if account is None:
        raise UnauthorizedException("Admin not authenticated.")

    if not await verify_password(payload.current_password, account["hashed_password"]):
        raise UnauthorizedException("Current password is incorrect.")

    await crud_admin_users.update(
        db=db,
        object={
            "hashed_password": get_password_hash(payload.new_password),
            "updated_at": datetime.now(UTC),
        },
        id=current_admin["id"],
    )
    await create_admin_audit_log(
        db=db,
        admin_user_id=current_admin["id"],
        action_type=AdminAuditLogActionType.ADMIN_PASSWORD_CHANGED.value,
        target_type=AdminAuditLogTargetType.ADMIN_AUTH.value,
        target_id=current_admin["id"],
        data={},
    )
    return {"message": "Password changed successfully."}
