from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio.session import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from ..config import settings


class Base(DeclarativeBase):
    pass


DATABASE_URL = settings.DATABASE_ASYNC_URL

engine_kwargs: dict[str, object] = {
    "echo": False,
    "future": True,
}

if settings.DATABASE_BACKEND.lower() != "sqlite":
    engine_kwargs["pool_pre_ping"] = settings.DATABASE_POOL_PRE_PING
    if settings.DATABASE_POOL_RECYCLE_SECONDS > 0:
        engine_kwargs["pool_recycle"] = settings.DATABASE_POOL_RECYCLE_SECONDS

async_engine = create_async_engine(DATABASE_URL, **engine_kwargs)

local_session = async_sessionmaker(bind=async_engine, class_=AsyncSession, expire_on_commit=False)


async def async_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with local_session() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise
