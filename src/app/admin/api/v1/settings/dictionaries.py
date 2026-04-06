from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...dependencies import require_admin_permission, require_any_admin_permission
from .....core.db.database import async_get_db
from .....modules.admin.dictionary.schema import DictionaryCreate, DictionaryRead, DictionaryUpdate
from .....modules.admin.dictionary.service import (
    create_dictionary,
    delete_dictionary,
    get_dictionary_model,
    list_dictionaries,
    serialize_dictionary,
    update_dictionary,
)

router = APIRouter(prefix="/dictionaries", tags=["admin-dictionaries"])


@router.get(
    "",
    response_model=list[DictionaryRead],
    dependencies=[Depends(require_any_admin_permission("岗位管理", "常量字典"))],
)
async def read_dictionaries(db: Annotated[AsyncSession, Depends(async_get_db)]) -> list[dict[str, Any]]:
    return await list_dictionaries(db)


@router.post("", response_model=DictionaryRead, status_code=201, dependencies=[Depends(require_admin_permission("常量字典"))])
async def create_dictionary_endpoint(
    payload: DictionaryCreate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, Any]:
    return await create_dictionary(payload, db)


@router.get(
    "/{dictionary_id}",
    response_model=DictionaryRead,
    dependencies=[Depends(require_any_admin_permission("岗位管理", "常量字典"))],
)
async def read_dictionary(dictionary_id: int, db: Annotated[AsyncSession, Depends(async_get_db)]) -> dict[str, Any]:
    dictionary = await get_dictionary_model(dictionary_id, db)
    return serialize_dictionary(dictionary)


@router.patch("/{dictionary_id}", response_model=DictionaryRead, dependencies=[Depends(require_admin_permission("常量字典"))])
async def update_dictionary_endpoint(
    dictionary_id: int,
    payload: DictionaryUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, Any]:
    return await update_dictionary(dictionary_id, payload, db)


@router.delete("/{dictionary_id}", dependencies=[Depends(require_admin_permission("常量字典"))])
async def delete_dictionary_endpoint(
    dictionary_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, str]:
    return await delete_dictionary(dictionary_id, db)
