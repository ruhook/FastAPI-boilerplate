from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .model import Payment


async def get_payment(*, db: AsyncSession, payment_id: int) -> Payment | None:
    return (await db.scalars(select(Payment).where(Payment.id == payment_id))).one_or_none()
