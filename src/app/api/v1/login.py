from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import JSONResponse

from ...core.auth_sessions import (
    InvalidRefreshTokenError,
    RefreshSessionError,
    RefreshTokenExpiredError,
    RefreshTokenReplayError,
    create_refresh_session,
    revoke_refresh_token,
    rotate_refresh_session,
)
from ...core.config import EnvironmentOption, settings
from ...core.db.database import async_get_db
from ...core.exceptions.http_exceptions import UnauthorizedException
from ...core.schemas import Token
from ...core.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    authenticate_user,
    create_access_token,
)
from ...modules.user.model import User

router = APIRouter(tags=["web-auth"])


@router.post("/login", response_model=Token)
async def login_for_access_token(
    request: Request,
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, str]:
    user = await authenticate_user(username_or_email=form_data.username, password=form_data.password, db=db)
    if not user:
        raise UnauthorizedException("Wrong username, email or password.")

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = await create_access_token(
        data={
            "sub": str(user["id"]),
            "portal": "web",
            "ver": int(user.get("token_version", 0)),
        },
        expires_delta=access_token_expires,
    )

    refresh = await create_refresh_session(
        db,
        portal="web",
        account_id=int(user["id"]),
        user_agent=request.headers.get("user-agent"),
    )
    max_age = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60

    response.set_cookie(
        key="refresh_token",
        value=refresh.token,
        httponly=True,
        secure=settings.ENVIRONMENT != EnvironmentOption.LOCAL,
        samesite="lax",
        max_age=max_age,
    )

    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/refresh", response_model=Token)
async def refresh_access_token(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, str] | JSONResponse:
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        raise UnauthorizedException("Refresh token missing.")

    try:
        rotated = await rotate_refresh_session(
            db,
            refresh_token,
            portal="web",
            user_agent=request.headers.get("user-agent"),
        )
    except (RefreshTokenExpiredError, RefreshTokenReplayError):
        return JSONResponse(status_code=401, content={"detail": "Invalid refresh token."})
    except (InvalidRefreshTokenError, RefreshSessionError):
        raise UnauthorizedException("Invalid refresh token.") from None

    user_result = await db.execute(
        select(User).where(
            User.id == rotated.session.account_id,
            User.is_deleted.is_(False),
        )
    )
    db_user = user_result.scalar_one_or_none()
    if db_user is None:
        raise UnauthorizedException("User not authenticated.")

    max_age = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    response.set_cookie(
        key="refresh_token",
        value=rotated.token,
        httponly=True,
        secure=settings.ENVIRONMENT != EnvironmentOption.LOCAL,
        samesite="lax",
        max_age=max_age,
    )

    new_access_token = await create_access_token(
        data={
            "sub": str(db_user.id),
            "portal": "web",
            "ver": db_user.token_version,
        },
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {"access_token": new_access_token, "token_type": "bearer"}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, str]:
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        await revoke_refresh_token(db, refresh_token, portal="web")
    response.delete_cookie(
        key="refresh_token",
        httponly=True,
        secure=settings.ENVIRONMENT != EnvironmentOption.LOCAL,
        samesite="lax",
    )
    return {"message": "Logged out successfully."}
