from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.db.database import async_get_db
from ...modules.candidate_application.schema import (
    CandidateApplicationSubmitRequest,
    CandidateApplicationSubmitResponse,
)
from ...modules.job.public_queries import (
    WebJobDetailRead,
    WebJobListPage,
)
from ...modules.job.public_queries import (
    get_public_job as query_public_job,
)
from ...modules.job.public_queries import (
    list_public_jobs as query_public_jobs,
)
from ...modules.job_progress.schema import (
    JobProgressAssessmentUploadResponse,
    JobProgressCandidateSignedContractUploadResponse,
)
from ...modules.job_progress.service import (
    submit_job_progress_assessment,
    submit_job_progress_candidate_signed_contract,
)
from ...modules.talent_profile.application_submission import create_application_and_sync_talent
from ..dependencies import get_current_user

router = APIRouter(prefix="/jobs", tags=["web-jobs"])


@router.get("", response_model=WebJobListPage)
async def list_public_jobs(
    db: Annotated[AsyncSession, Depends(async_get_db)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = Query(default=None),
    work_mode: str | None = Query(default=None),
    country: str | None = Query(default=None),
) -> WebJobListPage:
    return await query_public_jobs(
        db=db,
        page=page,
        page_size=page_size,
        keyword=keyword,
        work_mode=work_mode,
        country=country,
    )


@router.get("/{job_id}", response_model=WebJobDetailRead)
async def get_public_job(
    job_id: int,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> WebJobDetailRead:
    return await query_public_job(job_id=job_id, db=db)


@router.post("/{job_id}/apply", response_model=CandidateApplicationSubmitResponse)
async def submit_job_application(
    job_id: int,
    payload: CandidateApplicationSubmitRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    return await create_application_and_sync_talent(
        job_id=job_id,
        payload=payload,
        current_user=current_user,
        db=db,
    )


@router.post(
    "/{job_id}/assessment/upload",
    response_model=JobProgressAssessmentUploadResponse,
    status_code=201,
)
async def upload_job_assessment(
    job_id: int,
    file: Annotated[UploadFile, File(...)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    return await submit_job_progress_assessment(
        job_id=job_id,
        user_id=int(current_user["id"]),
        upload=file,
        db=db,
    )


@router.post(
    "/{job_id}/signed-contract/upload",
    response_model=JobProgressCandidateSignedContractUploadResponse,
    status_code=201,
)
async def upload_job_signed_contract(
    job_id: int,
    file: Annotated[UploadFile, File(...)],
    db: Annotated[AsyncSession, Depends(async_get_db)],
    current_user: Annotated[dict[str, Any], Depends(get_current_user)],
) -> dict[str, Any]:
    return await submit_job_progress_candidate_signed_contract(
        job_id=job_id,
        user_id=int(current_user["id"]),
        upload=file,
        db=db,
    )
