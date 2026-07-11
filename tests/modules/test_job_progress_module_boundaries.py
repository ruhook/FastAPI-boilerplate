import ast
import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_database_cleanup

MODULE_DIR = Path(__file__).resolve().parents[2] / "src/app/modules/job_progress"
IMPLEMENTATION_MODULES = {
    "assessment_workflow",
    "automation",
    "commands",
    "contract_workflow",
    "filtering",
    "mail_workflow",
    "normalization",
    "queries",
    "serialization",
    "state",
}
PUBLIC_OPERATIONS = {
    "build_locked_job_progress_query",
    "create_job_progress_for_application",
    "ensure_expected_progress_versions",
    "execute_job_progress_assessment_automation",
    "get_candidate_job_application_detail",
    "get_job_progress_by_application_id",
    "get_job_progress_models",
    "list_candidate_contracts",
    "list_candidate_job_applications",
    "list_job_progress",
    "mark_job_progress_assessment_invited",
    "move_job_progress_stage",
    "notify_job_progress_sign_contract",
    "serialize_job_progress",
    "submit_job_progress_assessment",
    "submit_job_progress_candidate_signed_contract",
    "sync_assessment_sent_at_from_mail_task",
    "update_job_progress_assessment_review",
    "update_job_progress_contract_record",
    "update_job_progress_language",
    "update_job_progress_note",
    "update_job_progress_onboarding",
    "upload_job_progress_company_sealed_contract",
    "upload_job_progress_contract_draft",
}


def test_service_is_thin_explicit_public_facade() -> None:
    service = importlib.import_module("src.app.modules.job_progress.service")
    tree = ast.parse((MODULE_DIR / "service.py").read_text(encoding="utf-8"))

    assert set(service.__all__) == PUBLIC_OPERATIONS
    assert all(callable(getattr(service, name)) for name in PUBLIC_OPERATIONS)
    assert not any(isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) for node in tree.body)
    assert not hasattr(service, "_evaluate_automation_rules")
    assert not hasattr(service, "_serialize_contract_record_data")


def test_implementation_modules_import_without_cycles_or_facade_dependency() -> None:
    for module_name in sorted(IMPLEMENTATION_MODULES):
        importlib.import_module(f"src.app.modules.job_progress.{module_name}")
        tree = ast.parse((MODULE_DIR / f"{module_name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").endswith("job_progress.service")
                assert node.module != "service"
                if (node.module or "").endswith("job_progress") or node.module is None:
                    assert all(alias.name != "service" for alias in node.names)
            if isinstance(node, ast.Import):
                assert all(not alias.name.endswith("job_progress.service") for alias in node.names)
