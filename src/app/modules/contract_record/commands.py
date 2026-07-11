from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.exceptions.http_exceptions import ConflictException, NotFoundException
from .const import ContractReviewStatus, ContractSigningStatus, ContractStatus
from .model import ContractRecord
from .policy import (
    ensure_activation_allowed,
    ensure_review_transition,
    ensure_signing_transition,
    ensure_status_transition,
)


async def get_locked_contract(*, db: AsyncSession, contract_record_id: int) -> ContractRecord:
    contract = (
        await db.scalars(
            select(ContractRecord)
            .where(
                ContractRecord.id == contract_record_id,
                ContractRecord.is_deleted.is_(False),
                ContractRecord.is_current.is_(True),
            )
            .with_for_update()
        )
    ).one_or_none()
    if contract is None:
        raise NotFoundException("Contract record not found.")
    return contract


async def record_draft_upload(
    *,
    db: AsyncSession,
    contract_record_id: int,
    asset_id: int,
    admin_user_id: int | None,
) -> ContractRecord:
    contract = await get_locked_contract(db=db, contract_record_id=contract_record_id)
    if ContractStatus(contract.contract_status) != ContractStatus.PENDING_ACTIVATION:
        raise ConflictException("Only pending contracts accept a new draft.")
    contract.draft_contract_asset_id = asset_id
    contract.contract_review_status = ContractReviewStatus.PENDING.value
    contract.signing_status = ContractSigningStatus.NOT_SENT.value
    contract.updated_by_admin_user_id = admin_user_id
    await db.flush()
    return contract


async def mark_contract_sent(
    *,
    db: AsyncSession,
    contract_record_id: int,
    admin_user_id: int | None,
) -> ContractRecord:
    contract = await get_locked_contract(db=db, contract_record_id=contract_record_id)
    current = ContractSigningStatus(contract.signing_status)
    ensure_signing_transition(current, ContractSigningStatus.SENT)
    if contract.draft_contract_asset_id is None:
        raise ConflictException("Contract must have a draft before it can be sent.")
    contract.signing_status = ContractSigningStatus.SENT.value
    contract.updated_by_admin_user_id = admin_user_id
    await db.flush()
    return contract


async def record_candidate_signature(
    *,
    db: AsyncSession,
    contract_record_id: int,
    asset_id: int,
    admin_user_id: int | None,
) -> ContractRecord:
    contract = await get_locked_contract(db=db, contract_record_id=contract_record_id)
    current = ContractSigningStatus(contract.signing_status)
    if current == ContractSigningStatus.CANDIDATE_SIGNED and contract.candidate_signed_contract_asset_id == asset_id:
        return contract
    ensure_signing_transition(current, ContractSigningStatus.CANDIDATE_SIGNED)
    contract.candidate_signed_contract_asset_id = asset_id
    contract.signing_status = ContractSigningStatus.CANDIDATE_SIGNED.value
    contract.contract_review_status = ContractReviewStatus.PENDING.value
    contract.updated_by_admin_user_id = admin_user_id
    await db.flush()
    return contract


async def review_contract(
    *,
    db: AsyncSession,
    contract_record_id: int,
    target: ContractReviewStatus,
    admin_user_id: int | None,
) -> ContractRecord:
    contract = await get_locked_contract(db=db, contract_record_id=contract_record_id)
    if ContractSigningStatus(contract.signing_status) != ContractSigningStatus.CANDIDATE_SIGNED:
        raise ConflictException("Contract review requires a candidate signature.")
    ensure_review_transition(ContractReviewStatus(contract.contract_review_status), target)
    contract.contract_review_status = target.value
    contract.updated_by_admin_user_id = admin_user_id
    await db.flush()
    return contract


async def seal_contract(
    *,
    db: AsyncSession,
    contract_record_id: int,
    asset_id: int,
    admin_user_id: int | None,
    effective_date: date | None = None,
) -> ContractRecord:
    contract = await get_locked_contract(db=db, contract_record_id=contract_record_id)
    if ContractReviewStatus(contract.contract_review_status) != ContractReviewStatus.APPROVED:
        raise ConflictException("Company seal requires review approval.")
    ensure_signing_transition(
        ContractSigningStatus(contract.signing_status),
        ContractSigningStatus.COMPANY_SEALED,
    )
    contract.company_sealed_contract_asset_id = asset_id
    contract.contract_attachment_asset_id = asset_id
    contract.signing_status = ContractSigningStatus.COMPANY_SEALED.value
    contract.effective_date = contract.effective_date or effective_date
    contract.updated_by_admin_user_id = admin_user_id
    await db.flush()
    return contract


async def activate_contract_record(
    *,
    db: AsyncSession,
    contract_record_id: int,
    admin_user_id: int | None,
) -> ContractRecord:
    contract = await get_locked_contract(db=db, contract_record_id=contract_record_id)
    status = ContractStatus(contract.contract_status)
    ensure_activation_allowed(
        contract_status=status,
        review_status=ContractReviewStatus(contract.contract_review_status),
        signing_status=ContractSigningStatus(contract.signing_status),
    )
    contract.contract_status = ContractStatus.ACTIVE.value
    contract.updated_by_admin_user_id = admin_user_id
    await db.flush()
    return contract


async def close_contract_record(
    *,
    db: AsyncSession,
    contract_record_id: int,
    target: ContractStatus,
    admin_user_id: int | None,
) -> ContractRecord:
    if target not in {ContractStatus.TERMINATED, ContractStatus.EXPIRED}:
        raise ValueError("Contract close target must be terminated or expired.")
    contract = await get_locked_contract(db=db, contract_record_id=contract_record_id)
    ensure_status_transition(ContractStatus(contract.contract_status), target)
    contract.contract_status = target.value
    contract.updated_by_admin_user_id = admin_user_id
    await db.flush()
    return contract
