from typing import Annotated, Any

from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_admin_user, require_admin_permission
from ....core.db.database import async_get_db
from ....modules.contract_record.schema import (
    ContractRecordListPage,
    ContractRecordResignResponse,
    ContractRecordUpdateRequest,
    ContractRecordUpdateResponse,
)
from ....modules.contract_record.service import (
    list_contract_records_for_admin,
    resign_contract_record_for_admin,
    update_contract_record_for_admin,
)

router = APIRouter(prefix="/contracts", tags=["admin-contracts"])


@router.get(
    "",
    response_model=ContractRecordListPage,
    dependencies=[Depends(require_admin_permission("合同管理"))],
)
async def read_contract_records(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    keyword: str | None = None,
    contract_status: str | None = None,
    company_id: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    return await list_contract_records_for_admin(
        admin_user_id=int(current_admin["id"]),
        db=db,
        page=page,
        page_size=page_size,
        keyword=keyword,
        contract_status=contract_status,
        company_id=company_id,
    )


@router.patch(
    "/{contract_record_id}",
    response_model=ContractRecordUpdateResponse,
    dependencies=[Depends(require_admin_permission("合同管理"))],
)
async def update_contract_record(
    contract_record_id: int,
    payload: ContractRecordUpdateRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    item = await update_contract_record_for_admin(
        contract_record_id=contract_record_id,
        admin_user_id=int(current_admin["id"]),
        db=db,
        contract_status=payload.contract_status,
        contract_type=payload.contract_type,
        agreement_ref_no=payload.agreement_ref_no,
        contractor_name=payload.contractor_name,
        rate=payload.rate,
        legal_entity=payload.legal_entity,
        worker_type=payload.worker_type,
        effective_date=payload.effective_date,
        end_date=payload.end_date,
        update_contract_type="contract_type" in payload.model_fields_set,
        update_agreement_ref_no="agreement_ref_no" in payload.model_fields_set,
        update_contractor_name="contractor_name" in payload.model_fields_set,
        update_rate="rate" in payload.model_fields_set,
        update_legal_entity="legal_entity" in payload.model_fields_set,
        update_worker_type="worker_type" in payload.model_fields_set,
        update_effective_date="effective_date" in payload.model_fields_set,
        update_end_date="end_date" in payload.model_fields_set,
    )
    return ContractRecordUpdateResponse(item=item).model_dump()


@router.post(
    "/{contract_record_id}/resign",
    response_model=ContractRecordResignResponse,
    dependencies=[Depends(require_admin_permission("合同管理"))],
)
async def resign_contract_record(
    contract_record_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
    file: Annotated[UploadFile, File(...)],
    contract_status: Annotated[str | None, Form()] = None,
    contract_type: Annotated[str | None, Form()] = None,
    agreement_ref_no: Annotated[str | None, Form()] = None,
    contractor_name: Annotated[str | None, Form()] = None,
    rate: Annotated[Decimal | None, Form()] = None,
    legal_entity: Annotated[str | None, Form()] = None,
    worker_type: Annotated[str | None, Form()] = None,
    effective_date: Annotated[date | None, Form()] = None,
    end_date: Annotated[date | None, Form()] = None,
) -> dict[str, Any]:
    item = await resign_contract_record_for_admin(
        contract_record_id=contract_record_id,
        admin_user_id=int(current_admin["id"]),
        db=db,
        upload=file,
        contract_status=contract_status,
        contract_type=contract_type,
        agreement_ref_no=agreement_ref_no,
        contractor_name=contractor_name,
        rate=rate,
        legal_entity=legal_entity,
        worker_type=worker_type,
        effective_date=effective_date,
        end_date=end_date,
    )
    return ContractRecordResignResponse(item=item).model_dump()
