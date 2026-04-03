import uuid as uuid_pkg
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Computed, DateTime, Integer, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7


class PrimaryKeyMixin:
    id: Mapped[int] = mapped_column(autoincrement=True, primary_key=True)


class AuditTimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=lambda: datetime.now(UTC),
        server_default=text("current_timestamp(0)"),
        default=None,
    )


class SoftDeleteFlagMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class ExtensionDataMixin:
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class UUIDKeyMixin:
    uuid: Mapped[uuid_pkg.UUID] = mapped_column(Uuid(as_uuid=True), default=uuid7, unique=True)


class StandardEntityMixin(PrimaryKeyMixin, AuditTimestampMixin):
    """Base columns shared by normal tables."""


class StandardSoftDeleteEntityMixin(StandardEntityMixin, SoftDeleteFlagMixin):
    """Standard entity with soft-delete support."""


class StandardUUIDEntityMixin(StandardEntityMixin, UUIDKeyMixin):
    """Standard entity with an additional UUID key."""


class StandardUUIDSoftDeleteEntityMixin(StandardUUIDEntityMixin, SoftDeleteFlagMixin):
    """Standard entity with both UUID and soft-delete support."""


class DataBackedEntityMixin(StandardEntityMixin, ExtensionDataMixin):
    """Entity with normal base columns plus a JSON extension bucket."""


class DataBackedSoftDeleteEntityMixin(DataBackedEntityMixin, SoftDeleteFlagMixin):
    """Data-backed entity with soft-delete support."""


# Backward-compatible aliases for older names still used in the codebase.
IDMixin = PrimaryKeyMixin
TimestampMixin = AuditTimestampMixin
SoftDeleteMixin = SoftDeleteFlagMixin
DataMixin = ExtensionDataMixin
UUIDMixin = UUIDKeyMixin
EntityMixin = StandardEntityMixin
SoftDeleteEntityMixin = StandardSoftDeleteEntityMixin
UUIDEntityMixin = StandardUUIDEntityMixin
UUIDSoftDeleteEntityMixin = StandardUUIDSoftDeleteEntityMixin
DataEntityMixin = DataBackedEntityMixin
DataSoftDeleteEntityMixin = DataBackedSoftDeleteEntityMixin


# Optional helpers for legacy "data + generated columns" tables.
# Keep these available for flexible/event-style tables, but do not use them by default
# for stable core entities such as user/admin_user/role.


def json_extract_expression(path: str) -> str:
    return f"JSON_EXTRACT(data, '$.{path}')"


def json_string_expression(path: str) -> str:
    return f"JSON_UNQUOTE({json_extract_expression(path)})"


def json_integer_expression(path: str) -> str:
    return f"CAST(NULLIF(JSON_UNQUOTE({json_extract_expression(path)}), 'null') AS SIGNED)"


def json_boolean_expression(path: str) -> str:
    return f"IFNULL(CAST(JSON_UNQUOTE({json_extract_expression(path)}) AS UNSIGNED), 0)"


def json_string_column(
    path: str,
    *,
    length: int,
    nullable: bool = True,
    index: bool = False,
    unique: bool = False,
) -> Mapped[str | None]:
    return mapped_column(
        String(length),
        Computed(json_string_expression(path), persisted=False),
        nullable=nullable,
        index=index,
        unique=unique,
    )


def json_integer_column(
    path: str,
    *,
    nullable: bool = True,
    index: bool = False,
    unique: bool = False,
) -> Mapped[int | None]:
    return mapped_column(
        Integer,
        Computed(json_integer_expression(path), persisted=False),
        nullable=nullable,
        index=index,
        unique=unique,
    )


def json_boolean_column(path: str, *, nullable: bool = False, index: bool = False) -> Mapped[bool]:
    return mapped_column(
        Boolean,
        Computed(json_boolean_expression(path), persisted=False),
        nullable=nullable,
        index=index,
    )


def json_json_column(path: str, *, nullable: bool = True) -> Mapped[dict[str, Any] | list[Any] | None]:
    return mapped_column(JSON, Computed(json_extract_expression(path), persisted=False), nullable=nullable)
