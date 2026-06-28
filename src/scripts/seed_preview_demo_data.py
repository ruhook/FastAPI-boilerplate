from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..app.core.db.database import async_engine, local_session
from ..app.core.security import get_password_hash
from ..app.modules.admin.admin_user.model import AdminUser
from ..app.modules.admin.company.model import AdminCompany, AdminCompanyProject
from ..app.modules.admin.form_template.model import AdminFormTemplate
from ..app.modules.admin.mail_account.model import MailAccount
from ..app.modules.admin.mail_signature.model import MailSignature
from ..app.modules.admin.mail_template.model import MailTemplate
from ..app.modules.admin.mail_template_category.model import MailTemplateCategory
from ..app.modules.admin.role.model import Role
from ..app.modules.assets.model import Asset
from ..app.modules.assets.schema import AssetUploadPayload
from ..app.modules.assets.service import create_asset_from_bytes
from ..app.modules.candidate_application.model import CandidateApplication
from ..app.modules.candidate_application_field_value.model import CandidateApplicationFieldValue
from ..app.modules.candidate_field.const import CANDIDATE_FIELD_CN_NAME_MAP, CandidateFieldKey
from ..app.modules.contract_record.const import (
    CONTRACT_STATUS_ACTIVE,
    CONTRACT_STATUS_PENDING_ACTIVATION,
    CONTRACT_TYPE_NORMAL,
)
from ..app.modules.contract_record.model import ContractRecord
from ..app.modules.job.const import JOB_DATA_FORM_FIELDS_KEY, JOB_DATA_LANGUAGES_KEY
from ..app.modules.job.model import Job
from ..app.modules.job_progress.const import JobProgressDataKey, RecruitmentScreeningMode, RecruitmentStage
from ..app.modules.job_progress.model import JobProgress
from ..app.modules.referral_bonus_model.model import ReferralBonusModel
from ..app.modules.talent_profile.model import TalentProfile
from ..app.modules.user.model import User
from .v2.shared import build_minimal_docx_bytes, build_minimal_pdf_bytes, build_minimal_xlsx_bytes

PREVIEW_PREFIX = "Preview QA"
ADMIN_PASSWORD = "PreviewAdmin123!"
CANDIDATE_PASSWORD = "PreviewCandidate123!"
_REFERENCED_MODELS = (MailAccount, MailSignature, MailTemplate, MailTemplateCategory, Role)


def _now() -> datetime:
    return datetime.now(UTC)


def _field_label(field_key: CandidateFieldKey) -> str:
    return CANDIDATE_FIELD_CN_NAME_MAP.get(field_key, field_key.value)


def _preview_form_field(
    field_key: CandidateFieldKey,
    *,
    field_type: str,
    required: bool,
    can_filter: bool,
) -> dict[str, object]:
    return {
        "key": field_key.value,
        "label": _field_label(field_key),
        "type": field_type,
        "required": required,
        "visible": True,
        "canFilter": can_filter,
    }


def _build_preview_form_fields() -> list[dict[str, object]]:
    return [
        _preview_form_field(
            CandidateFieldKey.FULL_NAME,
            field_type="text",
            required=True,
            can_filter=True,
        ),
        _preview_form_field(
            CandidateFieldKey.EMAIL,
            field_type="email",
            required=True,
            can_filter=True,
        ),
        _preview_form_field(
            CandidateFieldKey.RESUME_ATTACHMENT,
            field_type="attachment",
            required=True,
            can_filter=False,
        ),
    ]


def _csv_bytes(rows: list[list[str]]) -> bytes:
    rendered = "\n".join(",".join(f'"{value.replace(chr(34), chr(34) + chr(34))}"' for value in row) for row in rows)
    return ("\ufeff" + rendered + "\n").encode("utf-8")


async def _first_active(db: AsyncSession, model: Any, *conditions: Any) -> Any | None:
    result = await db.execute(select(model).where(model.is_deleted.is_(False), *conditions).limit(1))
    return result.scalar_one_or_none()


async def _ensure_admin(db: AsyncSession) -> AdminUser:
    existing = await _first_active(db, AdminUser, AdminUser.is_superuser.is_(True))
    if existing is not None:
        return existing

    admin = AdminUser(
        name="Preview Admin",
        username="previewadmin",
        email="preview.admin@example.com",
        hashed_password=get_password_hash(ADMIN_PASSWORD),
        phone=None,
        note="Seeded for preview QA data.",
        status="enabled",
        profile_image_url="",
        is_superuser=True,
        data={"preview_seed": True},
    )
    db.add(admin)
    await db.flush()
    return admin


async def _ensure_user(db: AsyncSession, *, name: str, username: str, email: str) -> User:
    existing = await _first_active(db, User, User.username == username)
    if existing is None:
        existing = await _first_active(db, User, User.email == email)
    if existing is not None:
        existing.name = name
        existing.email = email
        existing.profile_image_url = existing.profile_image_url or ""
        existing.data = {**(existing.data or {}), "preview_seed": True}
        return existing

    user = User(
        name=name,
        username=username,
        email=email,
        hashed_password=get_password_hash(CANDIDATE_PASSWORD),
        profile_image_url="",
        data={"preview_seed": True},
    )
    db.add(user)
    await db.flush()
    return user


async def _ensure_talent(db: AsyncSession, *, user: User, full_name: str, resume_asset_id: int | None) -> TalentProfile:
    existing = await _first_active(db, TalentProfile, TalentProfile.user_id == user.id)
    if existing is None:
        existing = TalentProfile(
            user_id=user.id,
            full_name=full_name,
            email=user.email,
            whatsapp="+1 555 0100",
            nationality="United States",
            location="United States",
            native_languages="English",
            additional_languages="Chinese",
            education="Bachelor completed",
            resume_asset_id=resume_asset_id,
            note="Seeded for preview QA data.",
            merge_strategy="preview_seed",
            data={"preview_seed": True},
        )
        db.add(existing)
    else:
        existing.full_name = full_name
        existing.email = user.email
        existing.resume_asset_id = resume_asset_id or existing.resume_asset_id
        existing.data = {**(existing.data or {}), "preview_seed": True}
    await db.flush()
    return existing


async def _ensure_company_project(db: AsyncSession) -> tuple[AdminCompany, AdminCompanyProject]:
    company = await _first_active(db, AdminCompany, AdminCompany.name == f"{PREVIEW_PREFIX} Company")
    if company is None:
        company = AdminCompany(
            name=f"{PREVIEW_PREFIX} Company",
            description="Seeded company for preview QA data.",
            data={"preview_seed": True},
        )
        db.add(company)
        await db.flush()

    project = await _first_active(
        db,
        AdminCompanyProject,
        AdminCompanyProject.company_id == company.id,
        AdminCompanyProject.name == f"{PREVIEW_PREFIX} Project",
    )
    if project is None:
        project = AdminCompanyProject(
            company_id=company.id,
            name=f"{PREVIEW_PREFIX} Project",
            data={"preview_seed": True},
        )
        db.add(project)
        await db.flush()
    return company, project


async def _ensure_form_template(db: AsyncSession) -> AdminFormTemplate:
    template = await _first_active(db, AdminFormTemplate, AdminFormTemplate.name == f"{PREVIEW_PREFIX} Form")
    if template is not None:
        template.fields = _build_preview_form_fields()
        template.data = {**(template.data or {}), "preview_seed": True}
        await db.flush()
        return template

    template = AdminFormTemplate(
        name=f"{PREVIEW_PREFIX} Form",
        description="Seeded form template for preview QA data.",
        fields=_build_preview_form_fields(),
        data={"preview_seed": True},
    )
    db.add(template)
    await db.flush()
    return template


async def _ensure_referral_model(db: AsyncSession, *, admin_user_id: int) -> ReferralBonusModel:
    model = await _first_active(db, ReferralBonusModel, ReferralBonusModel.name == f"{PREVIEW_PREFIX} Referral")
    if model is not None:
        return model

    model = ReferralBonusModel(
        name=f"{PREVIEW_PREFIX} Referral",
        status="active",
        currency="USD",
        reward_cap=Decimal("0.00"),
        created_by_admin_user_id=admin_user_id,
        updated_by_admin_user_id=admin_user_id,
        data={"preview_seed": True, "milestones": []},
    )
    db.add(model)
    await db.flush()
    return model


async def _ensure_job(
    db: AsyncSession,
    *,
    title: str,
    admin: AdminUser,
    company: AdminCompany,
    project: AdminCompanyProject,
    form_template: AdminFormTemplate,
    referral_model: ReferralBonusModel,
    assessment_enabled: bool,
) -> Job:
    job = await _first_active(db, Job, Job.title == title)
    if job is None:
        job = Job(
            title=title,
            company_id=company.id,
            project_id=project.id,
            referral_bonus_model_id=referral_model.id,
            country="United States",
            status="在招",
            work_mode="Remote",
            compensation_min=Decimal("6.00"),
            compensation_max=Decimal("12.00"),
            compensation_unit="Per Hour",
            description=f"<p>{title} seeded for preview QA.</p>",
            applicant_count=0,
            owner_admin_user_id=admin.id,
            form_template_id=form_template.id,
            assessment_enabled=assessment_enabled,
            data={
                "preview_seed": True,
                JOB_DATA_FORM_FIELDS_KEY: form_template.fields,
                JOB_DATA_LANGUAGES_KEY: ["English", "Chinese"],
            },
        )
        db.add(job)
    else:
        job.company_id = company.id
        job.project_id = project.id
        job.referral_bonus_model_id = referral_model.id
        job.owner_admin_user_id = admin.id
        job.form_template_id = form_template.id
        job.assessment_enabled = assessment_enabled
        job.applicant_count = 0
        job.data = {
            **(job.data or {}),
            "preview_seed": True,
            JOB_DATA_FORM_FIELDS_KEY: form_template.fields,
            JOB_DATA_LANGUAGES_KEY: ["English", "Chinese"],
        }
    await db.flush()
    return job


async def _hide_existing_preview_rows(db: AsyncSession) -> None:
    now = _now()
    job_result = await db.execute(
        select(Job.id).where(Job.is_deleted.is_(False), Job.title.like(f"{PREVIEW_PREFIX} -%"))
    )
    job_ids = [int(job_id) for job_id in job_result.scalars().all()]
    if not job_ids:
        return

    progress_result = await db.execute(
        select(JobProgress).where(JobProgress.job_id.in_(job_ids), JobProgress.is_deleted.is_(False))
    )
    for progress in progress_result.scalars().all():
        progress.is_deleted = True
        progress.deleted_at = now

    contract_result = await db.execute(
        select(ContractRecord).where(ContractRecord.job_id.in_(job_ids), ContractRecord.is_deleted.is_(False))
    )
    for contract in contract_result.scalars().all():
        contract.is_deleted = True
        contract.deleted_at = now
        contract.is_current = False

    application_result = await db.execute(
        select(CandidateApplication).where(
            CandidateApplication.job_id.in_(job_ids),
            CandidateApplication.is_deleted.is_(False),
        )
    )
    for application in application_result.scalars().all():
        application.is_deleted = True
        application.deleted_at = now

    await db.flush()


async def _create_asset(
    db: AsyncSession,
    *,
    original_name: str,
    content: bytes,
    mime_type: str,
    module: str,
    owner_type: str | None,
    owner_id: int | None,
) -> Asset:
    return await create_asset_from_bytes(
        db=db,
        payload=AssetUploadPayload(
            type="file",
            module=module,
            owner_type=owner_type,
            owner_id=owner_id,
        ),
        original_name=original_name,
        content=content,
        mime_type=mime_type,
        data={"preview_seed": True},
    )


async def _create_application_with_progress(
    db: AsyncSession,
    *,
    job: Job,
    user: User,
    talent: TalentProfile,
    stage: RecruitmentStage,
    full_name: str,
    resume_asset: Asset,
) -> tuple[CandidateApplication, JobProgress]:
    submitted_at = _now()
    application = CandidateApplication(
        user_id=user.id,
        job_id=job.id,
        form_template_id=job.form_template_id,
        job_snapshot_title=job.title,
        status="submitted",
        submitted_at=submitted_at,
        data={"preview_seed": True},
    )
    db.add(application)
    await db.flush()

    field_specs: list[tuple[CandidateFieldKey, str, str, int | None]] = [
        (CandidateFieldKey.FULL_NAME, full_name, "text", None),
        (CandidateFieldKey.EMAIL, user.email, "email", None),
        (CandidateFieldKey.COUNTRY_OF_RESIDENCE, "United States", "select", None),
        (CandidateFieldKey.NATIONALITY, "United States", "select", None),
        (CandidateFieldKey.NATIVE_LANGUAGES, "English", "multiselect", None),
        (CandidateFieldKey.ADDITIONAL_LANGUAGES, "Chinese", "multiselect", None),
        (CandidateFieldKey.EXPECTED_SALARY_USD_PER_HOUR, "6_10", "select", None),
        (CandidateFieldKey.RESUME_ATTACHMENT, resume_asset.original_name, "attachment", resume_asset.id),
    ]
    for index, (field_key, value, field_type, asset_id) in enumerate(field_specs, start=1):
        db.add(
            CandidateApplicationFieldValue(
                application_id=application.id,
                field_key=field_key.value,
                field_label=_field_label(field_key),
                field_type=field_type,
                catalog_key=field_key.value,
                raw_value=value,
                display_value=value,
                asset_id=asset_id,
                sort_order=index,
            )
        )

    progress = JobProgress(
        job_id=job.id,
        user_id=user.id,
        application_id=application.id,
        talent_profile_id=talent.id,
        current_stage=stage.value,
        screening_mode=RecruitmentScreeningMode.MANUAL.value,
        entered_stage_at=submitted_at,
        data={"preview_seed": True, JobProgressDataKey.JOB_LANGUAGES.value: ["English", "Chinese"]},
    )
    db.add(progress)
    job.applicant_count = int(job.applicant_count or 0) + 1
    talent.latest_applied_job_id = job.id
    talent.latest_applied_job_title = job.title
    talent.latest_applied_at = submitted_at
    talent.source_application_id = application.id
    talent.last_merged_at = submitted_at
    await db.flush()
    return application, progress


async def _seed_assessment_case(db: AsyncSession, *, job: Job) -> dict[str, Any]:
    user = await _ensure_user(
        db,
        name="Preview QA Assessment",
        username="previewqaassessment",
        email="preview.qa.assessment@example.com",
    )
    resume = await _create_asset(
        db,
        original_name="preview-qa-assessment-resume.pdf",
        content=build_minimal_pdf_bytes("Preview QA assessment resume"),
        mime_type="application/pdf",
        module="candidate_application",
        owner_type="user",
        owner_id=user.id,
    )
    talent = await _ensure_talent(db, user=user, full_name=user.name, resume_asset_id=resume.id)
    _, progress = await _create_application_with_progress(
        db,
        job=job,
        user=user,
        talent=talent,
        stage=RecruitmentStage.ASSESSMENT_REVIEW,
        full_name=user.name,
        resume_asset=resume,
    )

    csv_asset = await _create_asset(
        db,
        original_name="preview-assessment-first-submit.csv",
        content=_csv_bytes(
            [
                ["question", "answer", "score"],
                ["Translate welcome message", "Welcome to T-Maxx", "8"],
                ["Quality note", "Needs punctuation cleanup", "7"],
            ]
        ),
        mime_type="text/csv",
        module="job_progress",
        owner_type="user",
        owner_id=user.id,
    )
    xlsx_asset = await _create_asset(
        db,
        original_name="preview-assessment-latest.xlsx",
        content=build_minimal_xlsx_bytes(
            rows=[
                ["question", "answer", "score"],
                ["Entity extraction", "Company / Candidate / Rate", "9"],
                ["QA comment", "Ready for review in Feishu preview", "10"],
            ]
        ),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        module="job_progress",
        owner_type="user",
        owner_id=user.id,
    )
    first_submitted = _now()
    latest_submitted = _now()
    progress.data = {
        **(progress.data or {}),
        JobProgressDataKey.ASSESSMENT_INVITED_AT.value: first_submitted.isoformat(),
        JobProgressDataKey.ASSESSMENT_SENT_AT.value: first_submitted.isoformat(),
        JobProgressDataKey.ASSESSMENT_ATTACHMENT.value: xlsx_asset.original_name,
        JobProgressDataKey.ASSESSMENT_ATTACHMENT_ASSET_ID.value: int(xlsx_asset.id),
        JobProgressDataKey.ASSESSMENT_SUBMITTED_AT.value: latest_submitted.isoformat(),
        JobProgressDataKey.ASSESSMENT_SUBMISSIONS.value: [
            {
                "asset_id": int(csv_asset.id),
                "name": csv_asset.original_name,
                "submitted_at": first_submitted.isoformat(),
            },
            {
                "asset_id": int(xlsx_asset.id),
                "name": xlsx_asset.original_name,
                "submitted_at": latest_submitted.isoformat(),
            },
        ],
        JobProgressDataKey.ASSESSMENT_REVIEWER.value: "Preview Reviewer",
        JobProgressDataKey.ASSESSMENT_REVIEW_COMMENT.value: "Seeded file for Feishu spreadsheet preview.",
        JobProgressDataKey.QA_STATUS.value: "待质检",
    }
    await db.flush()
    return {
        "job_id": int(job.id),
        "progress_id": int(progress.id),
        "assets": {
            "assessment_csv_asset_id": int(csv_asset.id),
            "assessment_xlsx_asset_id": int(xlsx_asset.id),
        },
    }


async def _seed_contract_case(db: AsyncSession, *, job: Job, admin: AdminUser) -> dict[str, Any]:
    user = await _ensure_user(
        db,
        name="Preview QA Contract",
        username="previewqacontract",
        email="preview.qa.contract@example.com",
    )
    resume = await _create_asset(
        db,
        original_name="preview-qa-contract-resume.pdf",
        content=build_minimal_pdf_bytes("Preview QA contract resume"),
        mime_type="application/pdf",
        module="candidate_application",
        owner_type="user",
        owner_id=user.id,
    )
    talent = await _ensure_talent(db, user=user, full_name=user.name, resume_asset_id=resume.id)
    _, progress = await _create_application_with_progress(
        db,
        job=job,
        user=user,
        talent=talent,
        stage=RecruitmentStage.CONTRACT_POOL,
        full_name=user.name,
        resume_asset=resume,
    )

    draft = await _create_asset(
        db,
        original_name="preview-draft-contract.docx",
        content=build_minimal_docx_bytes("Preview draft contract. This should use the backend PDF export preview."),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        module="job_progress",
        owner_type="job_progress",
        owner_id=progress.id,
    )
    candidate_signed = await _create_asset(
        db,
        original_name="preview-candidate-signed-contract.xlsx",
        content=build_minimal_xlsx_bytes(
            rows=[
                ["section", "value"],
                ["candidate", user.name],
                ["rate", "8.50 USD / hour"],
                ["review", "Use Feishu spreadsheet preview here"],
            ]
        ),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        module="job_progress",
        owner_type="user",
        owner_id=user.id,
    )
    company_return = await _create_asset(
        db,
        original_name="preview-company-returned-contract.pdf",
        content=build_minimal_pdf_bytes("Preview company returned contract PDF"),
        mime_type="application/pdf",
        module="job_progress",
        owner_type="job_progress",
        owner_id=progress.id,
    )
    submitted_at = _now()
    progress.data = {
        **(progress.data or {}),
        JobProgressDataKey.ACCEPTED_RATE.value: "8.50",
        JobProgressDataKey.SIGNING_STATUS.value: "已通知人选签合同",
        JobProgressDataKey.CONTRACT_NUMBER.value: "PV-CONTRACT-001",
        JobProgressDataKey.SUBMITTED_CONTRACT_AT.value: submitted_at.isoformat(),
        JobProgressDataKey.NOTE.value: "Candidate signed contract is an xlsx file for Feishu preview.",
    }
    record = ContractRecord(
        user_id=user.id,
        user_snapshot_name=user.name,
        user_snapshot_email=user.email,
        talent_profile_id=talent.id,
        application_id=progress.application_id,
        job_id=job.id,
        job_progress_id=progress.id,
        job_snapshot_title=job.title,
        service_customer_company_id=job.company_id,
        service_customer_project_id=job.project_id,
        agreement_ref_no="PV-CONTRACT-001",
        contract_status=CONTRACT_STATUS_PENDING_ACTIVATION,
        contract_type=CONTRACT_TYPE_NORMAL,
        contractor_name=user.name,
        rate=Decimal("8.50"),
        legal_entity="T-Maxx International",
        worker_type="Contractor",
        effective_date=date.today(),
        end_date=date(date.today().year, 12, 31),
        draft_contract_asset_id=draft.id,
        candidate_signed_contract_asset_id=candidate_signed.id,
        company_sealed_contract_asset_id=company_return.id,
        contract_attachment_asset_id=company_return.id,
        parse_status="pending",
        version=1,
        is_current=True,
        created_by_admin_user_id=admin.id,
        updated_by_admin_user_id=admin.id,
        data={
            "preview_seed": True,
            "draft_contract_attachment_name": draft.original_name,
            "candidate_signed_contract_attachment_name": candidate_signed.original_name,
            "candidate_signed_contract_submitted_at": submitted_at.isoformat(),
            "company_sealed_contract_attachment_name": company_return.original_name,
            "contract_attachment_name": company_return.original_name,
            "contract_review": "待审核",
            "signing_status": "已通知人选签合同",
        },
    )
    db.add(record)
    await db.flush()
    return {
        "job_id": int(job.id),
        "progress_id": int(progress.id),
        "contract_record_id": int(record.id),
        "assets": {
            "draft_docx_asset_id": int(draft.id),
            "candidate_signed_xlsx_asset_id": int(candidate_signed.id),
            "company_return_pdf_asset_id": int(company_return.id),
        },
    }


async def _seed_generic_case(db: AsyncSession, *, job: Job, admin: AdminUser) -> dict[str, Any]:
    user = await _ensure_user(
        db,
        name="Preview QA Generic",
        username="previewqageneric",
        email="preview.qa.generic@example.com",
    )
    resume = await _create_asset(
        db,
        original_name="preview-qa-generic-resume.pdf",
        content=build_minimal_pdf_bytes("Preview QA generic resume"),
        mime_type="application/pdf",
        module="candidate_application",
        owner_type="user",
        owner_id=user.id,
    )
    talent = await _ensure_talent(db, user=user, full_name=user.name, resume_asset_id=resume.id)
    _, progress = await _create_application_with_progress(
        db,
        job=job,
        user=user,
        talent=talent,
        stage=RecruitmentStage.ACTIVE,
        full_name=user.name,
        resume_asset=resume,
    )

    id_sheet = await _create_asset(
        db,
        original_name="preview-id-attachment.xlsx",
        content=build_minimal_xlsx_bytes(
            rows=[
                ["field", "value"],
                ["document", "ID proof preview sample"],
                ["purpose", "Contracts page ID preview via Feishu"],
            ]
        ),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        module="job_progress",
        owner_type="user",
        owner_id=user.id,
    )
    draft = await _create_asset(
        db,
        original_name="preview-active-draft.pdf",
        content=build_minimal_pdf_bytes("Preview active draft PDF"),
        mime_type="application/pdf",
        module="job_progress",
        owner_type="job_progress",
        owner_id=progress.id,
    )
    candidate_signed = await _create_asset(
        db,
        original_name="preview-active-candidate-signed.docx",
        content=build_minimal_docx_bytes("Preview active signed DOCX. This should export to PDF."),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        module="job_progress",
        owner_type="user",
        owner_id=user.id,
    )
    contract_sheet = await _create_asset(
        db,
        original_name="preview-active-contract-attachment.xlsx",
        content=build_minimal_xlsx_bytes(
            rows=[
                ["contract item", "value"],
                ["status", "Active"],
                ["preview", "Contracts page main attachment should use Feishu"],
            ]
        ),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        module="contract",
        owner_type="admin_user",
        owner_id=admin.id,
    )
    submitted_at = _now()
    user.data = {
        **(user.data or {}),
        "payment_info": {
            **((user.data or {}).get("payment_info") or {}),
            "id_attachment_asset_id": int(id_sheet.id),
            "id_attachment_name": id_sheet.original_name,
        },
    }
    progress.data = {
        **(progress.data or {}),
        JobProgressDataKey.ONBOARDING_STATUS.value: "成功签约",
        JobProgressDataKey.ONBOARDING_DATE.value: date.today().isoformat(),
        JobProgressDataKey.NOTE.value: "Contract attachment and ID attachment are xlsx files for generic preview.",
    }
    record = ContractRecord(
        user_id=user.id,
        user_snapshot_name=user.name,
        user_snapshot_email=user.email,
        talent_profile_id=talent.id,
        application_id=progress.application_id,
        job_id=job.id,
        job_progress_id=progress.id,
        job_snapshot_title=job.title,
        service_customer_company_id=job.company_id,
        service_customer_project_id=job.project_id,
        agreement_ref_no="PV-ACTIVE-001",
        contract_status=CONTRACT_STATUS_ACTIVE,
        contract_type=CONTRACT_TYPE_NORMAL,
        contractor_name=user.name,
        rate=Decimal("9.00"),
        legal_entity="T-Maxx International",
        worker_type="Contractor",
        effective_date=date.today(),
        end_date=date(date.today().year, 12, 31),
        draft_contract_asset_id=draft.id,
        candidate_signed_contract_asset_id=candidate_signed.id,
        company_sealed_contract_asset_id=contract_sheet.id,
        contract_attachment_asset_id=contract_sheet.id,
        parse_status="pending",
        version=1,
        is_current=True,
        created_by_admin_user_id=admin.id,
        updated_by_admin_user_id=admin.id,
        data={
            "preview_seed": True,
            "draft_contract_attachment_name": draft.original_name,
            "candidate_signed_contract_attachment_name": candidate_signed.original_name,
            "candidate_signed_contract_submitted_at": submitted_at.isoformat(),
            "company_sealed_contract_attachment_name": contract_sheet.original_name,
            "contract_attachment_name": contract_sheet.original_name,
            "contract_review": "审核通过",
            "signing_status": "已完成签约",
        },
    )
    db.add(record)
    await db.flush()
    return {
        "job_id": int(job.id),
        "progress_id": int(progress.id),
        "contract_record_id": int(record.id),
        "assets": {
            "id_xlsx_asset_id": int(id_sheet.id),
            "contract_attachment_xlsx_asset_id": int(contract_sheet.id),
            "candidate_signed_docx_asset_id": int(candidate_signed.id),
            "draft_pdf_asset_id": int(draft.id),
        },
    }


async def seed_preview_demo_data() -> dict[str, Any]:
    async with local_session() as db:
        await _hide_existing_preview_rows(db)
        admin = await _ensure_admin(db)
        company, project = await _ensure_company_project(db)
        form_template = await _ensure_form_template(db)
        referral_model = await _ensure_referral_model(db, admin_user_id=int(admin.id))
        assessment_job = await _ensure_job(
            db,
            title=f"{PREVIEW_PREFIX} - 测试题表格预览",
            admin=admin,
            company=company,
            project=project,
            form_template=form_template,
            referral_model=referral_model,
            assessment_enabled=True,
        )
        contract_job = await _ensure_job(
            db,
            title=f"{PREVIEW_PREFIX} - 合同表格预览",
            admin=admin,
            company=company,
            project=project,
            form_template=form_template,
            referral_model=referral_model,
            assessment_enabled=False,
        )
        generic_job = await _ensure_job(
            db,
            title=f"{PREVIEW_PREFIX} - 通用附件预览",
            admin=admin,
            company=company,
            project=project,
            form_template=form_template,
            referral_model=referral_model,
            assessment_enabled=False,
        )

        assessment_case = await _seed_assessment_case(db, job=assessment_job)
        contract_case = await _seed_contract_case(db, job=contract_job, admin=admin)
        generic_case = await _seed_generic_case(db, job=generic_job, admin=admin)
        await db.commit()

        return {
            "admin_login_hint": {
                "username": admin.username,
                "password": "existing password" if admin.username != "previewadmin" else ADMIN_PASSWORD,
            },
            "candidate_password_for_seeded_users": CANDIDATE_PASSWORD,
            "paths": {
                "assessment_progress": f"/jobs/{assessment_case['job_id']}/progress",
                "contract_progress": f"/jobs/{contract_case['job_id']}/progress",
                "generic_progress": f"/jobs/{generic_case['job_id']}/progress",
                "contracts": "/contracts",
            },
            "cases": {
                "assessment": assessment_case,
                "contract": contract_case,
                "generic": generic_case,
            },
            "notes": [
                "Spreadsheet files are .xlsx/.csv and should be downloaded instead of previewed inline.",
                "PDF files preview locally in the browser.",
                "DOCX files use the backend /download-pdf preview path.",
            ],
        }


async def main() -> None:
    try:
        payload = await seed_preview_demo_data()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
