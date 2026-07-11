from datetime import date
from decimal import Decimal
from typing import Any

from ...core.exceptions.http_exceptions import BadRequestException
from ..admin.company.model import AdminCompany, AdminCompanyProject
from ..job.model import Job
from .const import normalize_contract_status
from .model import ContractRecord
from .schema import ContractRecordAssetRead, ContractRecordListItemRead


def _normalize_contract_status_or_400(value: str | None) -> str:
    try:
        return normalize_contract_status(value)
    except ValueError as exc:
        raise BadRequestException("Invalid contract status.") from exc


def _normalize_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip())
    except Exception:
        return None


def get_default_contract_end_date(effective_date: date | None) -> date | None:
    if effective_date is None:
        return None
    return date(effective_date.year, 12, 31)


def _serialize_contract_asset(asset_payload: dict[str, Any] | None) -> ContractRecordAssetRead | None:
    if not asset_payload:
        return None
    return ContractRecordAssetRead(
        asset_id=int(asset_payload["id"]),
        name=str(asset_payload["original_name"]),
        preview_url=asset_payload.get("preview_url"),
        download_url=asset_payload.get("download_url"),
        mime_type=asset_payload.get("mime_type"),
    )


def _extract_id_attachment_asset_id(user_data: dict[str, Any] | None) -> int | None:
    payment_info = (user_data or {}).get("payment_info")
    if not isinstance(payment_info, dict):
        return None
    raw_asset_id = payment_info.get("id_attachment_asset_id")
    if raw_asset_id is None or raw_asset_id == "" or raw_asset_id == 0:
        return None
    try:
        return int(raw_asset_id)
    except (TypeError, ValueError):
        return None


def serialize_contract_record(
    record: ContractRecord,
    *,
    job: Job,
    company: AdminCompany | None,
    project: AdminCompanyProject | None,
    asset_map: dict[int, dict[str, Any]],
    id_attachment_asset_id: int | None,
) -> ContractRecordListItemRead:
    return ContractRecordListItemRead(
        id=record.id,
        previous_contract_record_id=record.previous_contract_record_id,
        version=record.version,
        is_current=record.is_current,
        user_id=record.user_id,
        talent_profile_id=record.talent_profile_id,
        application_id=record.application_id,
        job_id=record.job_id,
        job_progress_id=record.job_progress_id,
        job_title=record.job_snapshot_title,
        service_customer_company_id=record.service_customer_company_id,
        service_customer_company_name=company.name if company is not None else None,
        service_customer_project_id=record.service_customer_project_id,
        service_customer_project_name=project.name if project is not None else None,
        agreement_ref_no=record.agreement_ref_no,
        contract_status=record.contract_status,
        contract_review_status=record.contract_review_status,
        signing_status=record.signing_status,
        contract_type=record.contract_type,
        contractor_name=record.contractor_name,
        contractor_email=record.user_snapshot_email,
        rate=record.rate,
        base_pay=record.base_pay,
        rate_unit=job.compensation_unit,
        legal_entity=record.legal_entity,
        worker_type=record.worker_type,
        effective_date=record.effective_date,
        end_date=record.end_date,
        contract_attachment=_serialize_contract_asset(asset_map.get(int(record.contract_attachment_asset_id or 0))),
        draft_contract_attachment=_serialize_contract_asset(asset_map.get(int(record.draft_contract_asset_id or 0))),
        candidate_signed_contract_attachment=_serialize_contract_asset(
            asset_map.get(int(record.candidate_signed_contract_asset_id or 0))
        ),
        company_sealed_contract_attachment=_serialize_contract_asset(
            asset_map.get(int(record.company_sealed_contract_asset_id or 0))
        ),
        id_attachment=_serialize_contract_asset(asset_map.get(int(id_attachment_asset_id or 0))),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
