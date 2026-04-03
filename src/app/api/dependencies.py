import logging
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.db.database import async_get_db
from ..core.exceptions.http_exceptions import UnauthorizedException
from ..core.security import TokenType, oauth2_scheme, verify_token
from ..modules.user.crud import crud_users
from ..modules.user.schema import UserAuth

logger = logging.getLogger(__name__)


async def _get_web_user_from_subject(
    username_or_email: str,
    db: AsyncSession,
    schema_to_select: type[UserAuth],
) -> dict[str, Any] | None:
    lookup_key = "email" if "@" in username_or_email else "username"
    return await crud_users.get(
        db=db,
        is_deleted=False,
        schema_to_select=schema_to_select,
        **{lookup_key: username_or_email},
    )


async def _get_authorization_token(request: Request, db: AsyncSession) -> tuple[str, Any]:
    authorization = request.headers.get("Authorization")
    if not authorization:
        raise UnauthorizedException("User not authenticated.")

    token_type, _, token_value = authorization.partition(" ")
    if token_type.lower() != "bearer" or not token_value:
        raise UnauthorizedException("User not authenticated.")

    token_data = await verify_token(token_value, TokenType.ACCESS)
    if token_data is None:
        raise UnauthorizedException("User not authenticated.")

    return token_value, token_data


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)], db: Annotated[AsyncSession, Depends(async_get_db)]
) -> dict[str, Any]:
    token_data = await verify_token(token, TokenType.ACCESS)
    if token_data is None or token_data.portal == "admin":
        raise UnauthorizedException("User not authenticated.")

    user = await _get_web_user_from_subject(token_data.username_or_email, db=db, schema_to_select=UserAuth)

    if user:
        return user

    raise UnauthorizedException("User not authenticated.")


async def get_optional_user(request: Request, db: AsyncSession = Depends(async_get_db)) -> dict | None:
    authorization = request.headers.get("Authorization")
    if not authorization:
        return None

    try:
        _, token_data = await _get_authorization_token(request, db)
        if token_data is None:
            return None

        if token_data.portal == "admin":
            return None

        return await _get_web_user_from_subject(token_data.username_or_email, db=db, schema_to_select=UserAuth)

    except HTTPException as http_exc:
        if http_exc.status_code != 401:
            logger.error(f"Unexpected HTTPException in get_optional_user: {http_exc.detail}")
        return None

    except Exception as exc:
        logger.error(f"Unexpected error in get_optional_user: {exc}")
        return None
