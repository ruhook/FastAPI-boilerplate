from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

import bcrypt
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import SecretStr

from ..modules.admin.admin_user.crud import crud_admin_users
from ..modules.user.crud import crud_users
from .config import settings
from .schemas import TokenData

SECRET_KEY: SecretStr = settings.SECRET_KEY
ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS = settings.REFRESH_TOKEN_EXPIRE_DAYS
ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES = settings.ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES
ADMIN_REFRESH_TOKEN_EXPIRE_DAYS = settings.ADMIN_REFRESH_TOKEN_EXPIRE_DAYS

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/login")
admin_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


class TokenType(str, Enum):
    ACCESS = "access"
    REFRESH = "refresh"


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    correct_password: bool = bcrypt.checkpw(plain_password.encode(), hashed_password.encode())
    return correct_password


def get_password_hash(password: str) -> str:
    hashed_password: str = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    return hashed_password


async def authenticate_user(username_or_email: str, password: str, db) -> dict[str, Any] | Literal[False]:
    if "@" in username_or_email:
        db_user = await crud_users.get(db=db, email=username_or_email, is_deleted=False)
    else:
        db_user = await crud_users.get(db=db, username=username_or_email, is_deleted=False)

    if not db_user:
        return False

    if not await verify_password(password, db_user["hashed_password"]):
        return False

    return db_user


async def authenticate_admin_user(
    username_or_email: str, password: str, db
) -> dict[str, Any] | Literal[False]:
    if "@" in username_or_email:
        db_user = await crud_admin_users.get(db=db, email=username_or_email, is_deleted=False)
    else:
        db_user = await crud_admin_users.get(db=db, username=username_or_email, is_deleted=False)

    if not db_user:
        return False

    if db_user["status"] != "enabled":
        return False

    if not await verify_password(password, db_user["hashed_password"]):
        return False

    return db_user


async def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    now = datetime.now(UTC).replace(tzinfo=None)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update(
        {
            "exp": expire,
            "iat": now,
            "jti": str(uuid4()),
            "token_type": TokenType.ACCESS,
        }
    )
    encoded_jwt: str = jwt.encode(to_encode, SECRET_KEY.get_secret_value(), algorithm=ALGORITHM)
    return encoded_jwt


async def create_refresh_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    now = datetime.now(UTC).replace(tzinfo=None)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update(
        {
            "exp": expire,
            "iat": now,
            "jti": str(uuid4()),
            "token_type": TokenType.REFRESH,
        }
    )
    encoded_jwt: str = jwt.encode(to_encode, SECRET_KEY.get_secret_value(), algorithm=ALGORITHM)
    return encoded_jwt


async def verify_token(token: str, expected_token_type: TokenType) -> TokenData | None:
    """Verify a JWT token and return TokenData if valid.

    Parameters
    ----------
    token: str
        The JWT token to be verified.
    expected_token_type: TokenType
        The expected type of token (access or refresh)
    Returns
    -------
    TokenData | None
        TokenData instance if the token is valid, None otherwise.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY.get_secret_value(), algorithms=[ALGORITHM])
        subject = payload.get("sub")
        portal = payload.get("portal")
        token_type = payload.get("token_type")
        token_version = payload.get("ver")
        issued_at = payload.get("iat")
        token_id = payload.get("jti")

        if (
            token_type != expected_token_type.value
            or portal not in {"web", "admin"}
            or not isinstance(subject, str)
            or not subject.isdigit()
            or not isinstance(token_version, int)
            or token_version < 0
            or not isinstance(issued_at, int)
            or not isinstance(token_id, str)
            or not token_id
        ):
            return None

        account_id = int(subject)
        if account_id == 0 and portal != "admin":
            return None

        return TokenData(
            account_id=account_id,
            portal=portal,
            token_version=token_version,
            token_id=token_id,
            issued_at=issued_at,
        )

    except JWTError:
        return None
