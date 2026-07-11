import json
from typing import Any

from ..candidate_field.const import CandidateFieldKey
from ..operation_log.const import OperationLogType
from ..operation_log.model import OperationLog

TALENT_FIELD_MAPPING: dict[str, str] = {
    CandidateFieldKey.FULL_NAME.value: "full_name",
    CandidateFieldKey.EMAIL.value: "email",
    CandidateFieldKey.WHATSAPP.value: "whatsapp",
    CandidateFieldKey.NATIONALITY.value: "nationality",
    CandidateFieldKey.COUNTRY_OF_RESIDENCE.value: "location",
    CandidateFieldKey.NATIVE_LANGUAGES.value: "native_languages",
    CandidateFieldKey.ADDITIONAL_LANGUAGES.value: "additional_languages",
    CandidateFieldKey.EDUCATION_STATUS.value: "education",
}

TALENT_ASSET_FIELD_MAPPING: dict[str, str] = {
    CandidateFieldKey.RESUME_ATTACHMENT.value: "resume_asset_id",
}


def _normalize_display_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, int | float | bool):
        return str(value)
    if isinstance(value, list):
        flattened = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(flattened) if flattened else None
    return str(value)


def _get_operation_log_title(log_type: str) -> str:
    if log_type == OperationLogType.CANDIDATE_APPLICATION_SUBMITTED.value:
        return "候选人提交报名"
    if log_type == OperationLogType.TALENT_PROFILE_INITIAL_AUTO_MERGE.value:
        return "首次自动创建人才快照"
    if log_type == OperationLogType.TALENT_PROFILE_LATEST_APPLICATION_UPDATED.value:
        return "更新最近申请岗位"
    if log_type == OperationLogType.TALENT_PROFILE_MANUAL_MERGE.value:
        return "手动合并人才快照"
    if log_type == OperationLogType.JOB_PROGRESS_CREATED.value:
        return "创建岗位流程记录"
    if log_type == OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value:
        return "岗位流程阶段变更"
    if log_type == OperationLogType.JOB_PROGRESS_ASSESSMENT_SUBMITTED.value:
        return "提交测试题附件"
    if log_type == OperationLogType.JOB_PROGRESS_CANDIDATE_SIGNED_CONTRACT_SUBMITTED.value:
        return "提交人选签回合同"
    if log_type == OperationLogType.JOB_PROGRESS_ASSESSMENT_REVIEW_UPDATED.value:
        return "更新测试题评审"
    if log_type == OperationLogType.JOB_PROGRESS_QA_REVIEW_UPDATED.value:
        return "更新质检结果"
    if log_type == OperationLogType.JOB_PROGRESS_CONTRACT_RECORD_UPDATED.value:
        return "更新合同信息"
    if log_type == OperationLogType.JOB_PROGRESS_CONTRACT_DRAFT_UPLOADED.value:
        return "上传待签合同"
    if log_type == OperationLogType.JOB_PROGRESS_COMPANY_SEALED_CONTRACT_UPLOADED.value:
        return "上传公司签回合同"
    if log_type == OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_CREATED.value:
        return "自动邮件任务已创建"
    if log_type == OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_SKIPPED.value:
        return "自动邮件已跳过"
    if log_type == OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_FAILED.value:
        return "自动邮件创建失败"
    if log_type == OperationLogType.REFERRAL_CREATED.value:
        return "创建邀请关系"
    return log_type


def _get_operation_log_actor_type(log_type: str) -> str:
    if log_type == OperationLogType.CANDIDATE_APPLICATION_SUBMITTED.value:
        return "candidate"
    if log_type == OperationLogType.JOB_PROGRESS_CANDIDATE_SIGNED_CONTRACT_SUBMITTED.value:
        return "candidate"
    if log_type == OperationLogType.TALENT_PROFILE_MANUAL_MERGE.value:
        return "admin"
    if log_type == OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value:
        return "system"
    if log_type in {
        OperationLogType.JOB_PROGRESS_ASSESSMENT_REVIEW_UPDATED.value,
        OperationLogType.JOB_PROGRESS_QA_REVIEW_UPDATED.value,
        OperationLogType.JOB_PROGRESS_CONTRACT_RECORD_UPDATED.value,
        OperationLogType.JOB_PROGRESS_CONTRACT_DRAFT_UPLOADED.value,
        OperationLogType.JOB_PROGRESS_COMPANY_SEALED_CONTRACT_UPLOADED.value,
    }:
        return "admin"
    return "system"


def _get_operation_log_status_label(log: OperationLog) -> str | None:
    data = log.data or {}
    if log.log_type == OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value:
        value = data.get("to_stage_cn_name") or data.get("to_stage")
        return str(value) if value else None
    value = data.get("current_stage_cn_name") or data.get("current_stage")
    return str(value) if value else None


def _build_operation_log_summary(log: OperationLog, job_title: str | None) -> str:
    data = log.data or {}
    resolved_job_title = job_title or data.get("job_title") or "-"

    if log.log_type == OperationLogType.CANDIDATE_APPLICATION_SUBMITTED.value:
        count = data.get("submitted_items_count")
        return f"提交了 {resolved_job_title} 的报名表（字段数 {count or 0}）"
    if log.log_type == OperationLogType.TALENT_PROFILE_INITIAL_AUTO_MERGE.value:
        merged_fields = data.get("merged_fields") or []
        application_id = log.application_id or data.get("application_id") or "-"
        return f"系统根据申请 #{application_id} 自动创建人才快照，合并了 {len(merged_fields)} 个字段"
    if log.log_type == OperationLogType.TALENT_PROFILE_LATEST_APPLICATION_UPDATED.value:
        return f"最近申请岗位更新为 {resolved_job_title}"
    if log.log_type == OperationLogType.TALENT_PROFILE_MANUAL_MERGE.value:
        merged_fields = data.get("merged_fields") or []
        application_id = log.application_id or data.get("application_id") or "-"
        return f"从申请 #{application_id} 手动合并了 {len(merged_fields)} 个字段"
    if log.log_type == OperationLogType.JOB_PROGRESS_CREATED.value:
        stage_cn_name = data.get("current_stage_cn_name") or data.get("current_stage") or "-"
        return f"{resolved_job_title} 已创建流程记录，初始阶段为 {stage_cn_name}"
    if log.log_type == OperationLogType.JOB_PROGRESS_STAGE_CHANGED.value:
        from_stage = data.get("from_stage_cn_name") or data.get("from_stage") or "-"
        to_stage = data.get("to_stage_cn_name") or data.get("to_stage") or "-"
        return f"{resolved_job_title} 从 {from_stage} 流转到 {to_stage}"
    if log.log_type == OperationLogType.JOB_PROGRESS_ASSESSMENT_SUBMITTED.value:
        attachment_name = data.get("assessment_attachment") or "-"
        return f"{resolved_job_title} 已提交测试题附件：{attachment_name}"
    if log.log_type == OperationLogType.JOB_PROGRESS_CANDIDATE_SIGNED_CONTRACT_SUBMITTED.value:
        attachment_name = data.get("submitted_contract_attachment") or "-"
        return f"{resolved_job_title} 已提交人选签回合同：{attachment_name}"
    if log.log_type == OperationLogType.JOB_PROGRESS_ASSESSMENT_REVIEW_UPDATED.value:
        updated_fields = data.get("updated_fields") or {}
        field_count = len(updated_fields) if isinstance(updated_fields, dict) else 0
        return f"{resolved_job_title} 已更新测试题评审信息（变更字段 {field_count} 项）"
    if log.log_type == OperationLogType.JOB_PROGRESS_QA_REVIEW_UPDATED.value:
        updated_fields = data.get("updated_fields") or {}
        field_count = len(updated_fields) if isinstance(updated_fields, dict) else 0
        return f"{resolved_job_title} 已更新质检信息（变更字段 {field_count} 项）"
    if log.log_type == OperationLogType.JOB_PROGRESS_CONTRACT_RECORD_UPDATED.value:
        updated_fields = data.get("contract_updated_fields") or []
        field_count = len(updated_fields) if isinstance(updated_fields, list) else 0
        return f"{resolved_job_title} 已更新合同信息（变更字段 {field_count} 项）"
    if log.log_type == OperationLogType.JOB_PROGRESS_CONTRACT_DRAFT_UPLOADED.value:
        attachment_name = data.get("contract_draft_attachment") or "-"
        return f"{resolved_job_title} 已上传待签合同：{attachment_name}"
    if log.log_type == OperationLogType.JOB_PROGRESS_COMPANY_SEALED_CONTRACT_UPLOADED.value:
        attachment_name = data.get("company_sealed_contract_attachment") or "-"
        return f"{resolved_job_title} 已上传公司签回合同：{attachment_name}"
    if log.log_type == OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_CREATED.value:
        target_stage = data.get("target_stage_cn_name") or data.get("target_stage") or "-"
        return f"{resolved_job_title} 已创建自动邮件任务（目标阶段：{target_stage}）"
    if log.log_type == OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_SKIPPED.value:
        target_stage = data.get("target_stage_cn_name") or data.get("target_stage") or "-"
        reason = data.get("reason") or "-"
        return f"{resolved_job_title} 自动邮件已跳过（目标阶段：{target_stage}，原因：{reason}）"
    if log.log_type == OperationLogType.JOB_PROGRESS_STAGE_MAIL_TASK_FAILED.value:
        target_stage = data.get("target_stage_cn_name") or data.get("target_stage") or "-"
        reason = data.get("reason") or "-"
        return f"{resolved_job_title} 自动邮件创建失败（目标阶段：{target_stage}，原因：{reason}）"
    if log.log_type == OperationLogType.REFERRAL_CREATED.value:
        referrer_email = data.get("referrer_email") or "-"
        return f"通过邀请链接建立推荐关系，邀请者邮箱：{referrer_email}"
    return json.dumps(data, ensure_ascii=False) if data else "-"


def _serialize_raw_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    if isinstance(value, int | float | bool):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


def _is_blank_application_value(value: Any, display_value: str | None) -> bool:
    if value is None:
        return not (display_value or "").strip()
    if isinstance(value, str):
        return not value.strip() and not (display_value or "").strip()
    if isinstance(value, list):
        return not [item for item in value if str(item).strip()]
    return False


def _normalize_option_values(raw_options: Any) -> set[str]:
    if not isinstance(raw_options, list):
        return set()
    normalized: set[str] = set()
    for option in raw_options:
        if isinstance(option, dict):
            for key in ("value", "label"):
                value = str(option.get(key) or "").strip()
                if value:
                    normalized.add(value)
            continue
        value = str(option or "").strip()
        if value:
            normalized.add(value)
    return normalized


def _normalize_submitted_option_values(value: Any, display_value: str | None) -> list[str]:
    if isinstance(value, list):
        values = [str(item).strip() for item in value if str(item).strip()]
    elif value is None:
        values = []
    else:
        normalized = str(value).strip()
        values = [normalized] if normalized else []
    if not values and display_value:
        values = [item.strip() for item in display_value.split(",") if item.strip()]
    return values

