from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

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
from ..dependencies import get_current_admin_user, require_admin_permission

router = APIRouter(prefix="/contracts", tags=["admin-contracts"])


def _parse_decimal_form_value(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _parse_date_form_value(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


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
    advanced_filter: str | None = Query(default=None),
) -> dict[str, Any]:
    return await list_contract_records_for_admin(
        admin_user_id=int(current_admin["id"]),
        db=db,
        page=page,
        page_size=page_size,
        keyword=keyword,
        contract_status=contract_status,
        company_id=company_id,
        advanced_filter=advanced_filter,
    )


@router.patch(
    "/{contract_record_id}",
    response_model=ContractRecordUpdateResponse,
    dependencies=[Depends(require_admin_permission("合同管理"))],
)
async def update_contract_record(
    contract_record_id: int,
    request: Request,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_admin: Annotated[dict[str, Any], Depends(get_current_admin_user)],
) -> dict[str, Any]:
    content_type = request.headers.get("content-type", "")
    latest_contract_upload: UploadFile | None = None
    fields_set: set[str]

    if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        payload = ContractRecordUpdateRequest(
            contract_status=form.get("contract_status"),
            contract_type=form.get("contract_type"),
            agreement_ref_no=form.get("agreement_ref_no"),
            contractor_name=form.get("contractor_name"),
            rate=_parse_decimal_form_value(form.get("rate")),
            base_pay=_parse_decimal_form_value(form.get("base_pay")),
            legal_entity=form.get("legal_entity"),
            worker_type=form.get("worker_type"),
            effective_date=_parse_date_form_value(form.get("effective_date")),
            end_date=_parse_date_form_value(form.get("end_date")),
        )
        fields_set = {
            key
            for key in [
                "contract_status",
                "contract_type",
                "agreement_ref_no",
                "contractor_name",
                "rate",
                "base_pay",
                "legal_entity",
                "worker_type",
                "effective_date",
                "end_date",
            ]
            if key in form
        }
        upload_value = form.get("latest_contract_file")
        if hasattr(upload_value, "filename") and hasattr(upload_value, "read"):
            latest_contract_upload = upload_value
    else:
        body = await request.json()
        payload = ContractRecordUpdateRequest.model_validate(body)
        fields_set = payload.model_fields_set

    item = await update_contract_record_for_admin(
        contract_record_id=contract_record_id,
        admin_user_id=int(current_admin["id"]),
        db=db,
        contract_status=payload.contract_status,
        contract_type=payload.contract_type,
        agreement_ref_no=payload.agreement_ref_no,
        contractor_name=payload.contractor_name,
        rate=payload.rate,
        base_pay=payload.base_pay,
        legal_entity=payload.legal_entity,
        worker_type=payload.worker_type,
        effective_date=payload.effective_date,
        end_date=payload.end_date,
        latest_contract_upload=latest_contract_upload,
        update_contract_type="contract_type" in fields_set,
        update_agreement_ref_no="agreement_ref_no" in fields_set,
        update_contractor_name="contractor_name" in fields_set,
        update_rate="rate" in fields_set,
        update_base_pay="base_pay" in fields_set,
        update_legal_entity="legal_entity" in fields_set,
        update_worker_type="worker_type" in fields_set,
        update_effective_date="effective_date" in fields_set,
        update_end_date="end_date" in fields_set,
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
    base_pay: Annotated[Decimal | None, Form()] = None,
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
        base_pay=base_pay,
        legal_entity=legal_entity,
        worker_type=worker_type,
        effective_date=effective_date,
        end_date=end_date,
    )
    return ContractRecordResignResponse(item=item).model_dump()
