from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .model import AdminAuditLog


async def create_admin_audit_log(
    *,
    db: AsyncSession,
    admin_user_id: int,
    action_type: str,
    target_type: str | None = None,
    target_id: int | None = None,
    data: dict[str, Any] | None = None,
) -> AdminAuditLog:
    log = AdminAuditLog(
        admin_user_id=admin_user_id,
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        data=data or {},
    )
    db.add(log)
    await db.flush()
    return log
