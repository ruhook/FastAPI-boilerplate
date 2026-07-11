from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.exceptions.http_exceptions import DuplicateValueException
from src.app.modules.admin.company.model import AdminCompany
from src.app.modules.admin.company.schema import CompanyCreate
from src.app.modules.admin.company.service import create_company
from src.app.modules.admin.dictionary.model import AdminDictionary
from src.app.modules.admin.dictionary.schema import DictionaryCreate
from src.app.modules.admin.dictionary.service import create_dictionary

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_company_integrity_translation_preserves_outer_transaction_work(
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex
    duplicate_name = f"deleted-company-{suffix}"
    db_session.add(AdminCompany(name=duplicate_name, is_deleted=True, data={}))
    await db_session.commit()

    marker = AdminCompany(name=f"company-marker-{suffix}", data={})
    db_session.add(marker)

    with pytest.raises(DuplicateValueException):
        await create_company(
            CompanyCreate(name=duplicate_name),
            db_session,
            admin_user_id=int(superadmin_credentials["id"]),
        )

    assert await db_session.scalar(select(AdminCompany).where(AdminCompany.name == marker.name)) is marker
    await db_session.rollback()


async def test_dictionary_integrity_translation_preserves_outer_transaction_work(
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex
    duplicate_key = f"deleted-dictionary-{suffix}"
    db_session.add(
        AdminDictionary(
            key=duplicate_key,
            label=f"Deleted Dictionary {suffix}",
            options=[],
            is_deleted=True,
            data={},
        )
    )
    await db_session.commit()

    marker = AdminDictionary(
        key=f"dictionary-marker-{suffix}",
        label=f"Dictionary Marker {suffix}",
        options=[],
        data={},
    )
    db_session.add(marker)

    with pytest.raises(DuplicateValueException):
        await create_dictionary(
            DictionaryCreate(key=duplicate_key, label=f"New Dictionary {suffix}"),
            db_session,
            admin_user_id=int(superadmin_credentials["id"]),
        )

    assert await db_session.scalar(select(AdminDictionary).where(AdminDictionary.key == marker.key)) is marker
    await db_session.rollback()
