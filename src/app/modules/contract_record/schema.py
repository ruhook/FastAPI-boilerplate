from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class ContractRecordAssetRead(BaseModel):
    asset_id: int
    name: str
    preview_url: str | None = None
    download_url: str | None = None
    mime_type: str | None = None


class ContractRecordListItemRead(BaseModel):
    id: int
    previous_contract_record_id: int | None = None
    version: int
    is_current: bool
    user_id: int
    talent_profile_id: int | None = None
    application_id: int | None = None
    job_id: int
    job_progress_id: int
    job_title: str | None = None
    service_customer_company_id: int | None = None
    service_customer_company_name: str | None = None
    service_customer_project_id: int | None = None
    service_customer_project_name: str | None = None
    agreement_ref_no: str | None = None
    contract_status: str
    contract_type: str
    contractor_name: str | None = None
    contractor_email: str | None = None
    rate: Decimal | None = None
    rate_unit: str | None = None
    legal_entity: str
    worker_type: str
    effective_date: date | None = None
    end_date: date | None = None
    contract_attachment: ContractRecordAssetRead | None = None
    draft_contract_attachment: ContractRecordAssetRead | None = None
    candidate_signed_contract_attachment: ContractRecordAssetRead | None = None
    company_sealed_contract_attachment: ContractRecordAssetRead | None = None
    id_attachment: ContractRecordAssetRead | None = None
    contract_review: str | None = None
    signing_status: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class ContractRecordListPage(BaseModel):
    items: list[ContractRecordListItemRead] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class ContractRecordUpdateRequest(BaseModel):
    contract_status: str | None = None
    contract_type: str | None = None
    agreement_ref_no: str | None = None
    contractor_name: str | None = None
    rate: Decimal | None = None
    legal_entity: str | None = None
    worker_type: str | None = None
    effective_date: date | None = None
    end_date: date | None = None


class ContractRecordUpdateResponse(BaseModel):
    item: ContractRecordListItemRead


class ContractRecordResignResponse(BaseModel):
    item: ContractRecordListItemRead
