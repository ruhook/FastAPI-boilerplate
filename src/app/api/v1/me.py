from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db.database import async_get_db
from ...modules.job_progress.schema import (
    CandidateContractListPage,
    CandidateJobApplicationDetailRead,
    CandidateJobApplicationListPage,
)
from ...modules.job_progress.service import (
    get_candidate_job_application_detail,
    list_candidate_contracts,
    list_candidate_job_applications,
)
from ...modules.payment.queries import list_candidate_payments
from ...modules.payment.schema import CandidatePaymentListPage, PaymentListQuery
from ...modules.project_timesheet_record.queries import list_candidate_timesheet_workspace
from ...modules.project_timesheet_record.schema import CandidateTimesheetWorkspaceRead
from ...modules.referral.schema import CandidateReferralDashboardRead
from ...modules.referral.service import get_candidate_referral_dashboard
from ...modules.user.payment_settings import (
    CandidatePaymentSettingsRead,
    CandidatePaymentSettingsUpdate,
    get_candidate_payment_settings,
    update_candidate_payment_settings,
)
from ..dependencies import get_current_user

router = APIRouter(prefix="/me", tags=["web-me"])




@router.get("/applications", response_model=CandidateJobApplicationListPage)
async def read_my_applications(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    keyword: str | None = Query(default=None),
    current_stage: str | None = Query(default=None),
    needs_action_only: bool = Query(default=False),
) -> dict:
    return await list_candidate_job_applications(
        user_id=int(current_user["id"]),
        page=page,
        page_size=page_size,
        keyword=keyword,
        current_stage=current_stage,
        needs_action_only=needs_action_only,
        db=db,
    )


@router.get("/contracts", response_model=CandidateContractListPage)
async def read_my_contracts(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    keyword: str | None = Query(default=None),
) -> dict:
    return await list_candidate_contracts(
        user_id=int(current_user["id"]),
        page=page,
        page_size=page_size,
        keyword=keyword,
        db=db,
    )


@router.get("/timesheets", response_model=CandidateTimesheetWorkspaceRead)
async def read_my_timesheets(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    bonus_month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
) -> dict:
    return await list_candidate_timesheet_workspace(
        user_id=int(current_user["id"]),
        start_date=start_date,
        end_date=end_date,
        bonus_month=bonus_month,
        db=db,
    )


@router.get("/referrals", response_model=CandidateReferralDashboardRead)
async def read_my_referrals(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    return await get_candidate_referral_dashboard(
        user_id=int(current_user["id"]),
        db=db,
    )


@router.get("/payments", response_model=CandidatePaymentListPage)
async def read_my_payments(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    month: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
    payment_type: str | None = Query(default=None),
) -> CandidatePaymentListPage:
    return await list_candidate_payments(
        db=db,
        user_id=int(current_user["id"]),
        query=PaymentListQuery(
            page=page,
            page_size=page_size,
            month=month,
            payment_type=payment_type,
        ),
    )


@router.get("/payment-settings", response_model=CandidatePaymentSettingsRead)
async def read_my_payment_settings(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    return (await get_candidate_payment_settings(user_id=int(current_user["id"]), db=db)).model_dump()


@router.patch("/payment-settings", response_model=CandidatePaymentSettingsRead)
async def update_my_payment_settings(
    payload: CandidatePaymentSettingsUpdate,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    return (
        await update_candidate_payment_settings(
            user_id=int(current_user["id"]),
            payload=payload,
            db=db,
        )
    ).model_dump()


@router.get("/applications/{application_id}", response_model=CandidateJobApplicationDetailRead)
async def read_my_application_detail(
    application_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict, Depends(get_current_user)],
) -> dict:
    return await get_candidate_job_application_detail(
        user_id=int(current_user["id"]),
        application_id=application_id,
        db=db,
    )
