import ast
from pathlib import Path

import pytest

from src.app.modules.job_progress.const import JobProgressDataKey, RecruitmentStage
from src.app.modules.job_progress.model import JobProgress
from src.app.modules.talent_profile.model import TalentProfile
from src.app.modules.talent_profile.pool_fields import (
    TALENT_STATUS_ACTIVE,
    TALENT_STATUS_OVERRIDE_KEY,
    derive_talent_status,
)

pytestmark = pytest.mark.no_database_cleanup

MODULE_DIR = Path(__file__).parents[2] / "src/app/modules/talent_profile"


def _relative_imports(module_name: str) -> set[str]:
    tree = ast.parse((MODULE_DIR / f"{module_name}.py").read_text())
    return {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level > 0
    }


def test_talent_profile_module_has_explicit_use_case_boundaries() -> None:
    assert not (MODULE_DIR / "service.py").exists()
    assert {
        "application_submission.py",
        "merge.py",
        "commands.py",
        "queries.py",
        "serialization.py",
    } <= {path.name for path in MODULE_DIR.glob("*.py")}

    assert "commands" not in _relative_imports("queries")
    assert "merge" not in _relative_imports("queries")
    assert "application_submission" not in _relative_imports("queries")
    assert "application_submission" not in _relative_imports("merge")


def test_talent_status_override_is_an_indexed_typed_column() -> None:
    column = TalentProfile.__table__.c.status_override

    assert column.nullable is True
    assert column.index is True


def test_talent_status_does_not_fall_back_to_legacy_json_override() -> None:
    talent = TalentProfile(
        user_id=1,
        data={TALENT_STATUS_OVERRIDE_KEY: "on_leave"},
    )
    progress = JobProgress(
        job_id=1,
        user_id=1,
        application_id=1,
        talent_profile_id=1,
        current_stage=RecruitmentStage.ACTIVE.value,
        data={JobProgressDataKey.ONBOARDING_DATE.value: "2026-07-11"},
    )

    status, _, editable = derive_talent_status(talent=talent, progress=progress)

    assert status == TALENT_STATUS_ACTIVE
    assert editable is True
