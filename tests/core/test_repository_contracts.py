import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_database_cleanup

ROOT = Path(__file__).resolve().parents[2]


def test_only_canonical_runtime_examples_remain() -> None:
    assert not (ROOT / "src/app/main.py").exists()
    for relative in (
        "scripts/local_with_uvicorn",
        "scripts/gunicorn_managing_uvicorn_workers",
        "scripts/production_with_nginx",
    ):
        assert not (ROOT / relative).exists()

    tracked_text = "\n".join(
        path.read_text(encoding="utf-8")
        for base in (ROOT / "deploy", ROOT / "scripts")
        for path in base.rglob("*")
        if path.is_file()
    ).lower()
    for obsolete in ("app.main:app", "poetry", "postgres:13", "command: arq"):
        assert obsolete not in tracked_text


def test_ci_uses_frozen_uv_lock() -> None:
    for workflow in (ROOT / ".github/workflows").glob("*.yml"):
        content = workflow.read_text(encoding="utf-8")
        assert "uv sync --frozen --all-extras --all-groups" in content
        assert "uv pip install" not in content

        for command in ("pytest", "ruff", "mypy", "alembic"):
            assert f"uv run {command}" not in content


def test_production_env_example_lists_required_foundation_settings() -> None:
    content = (ROOT / "deploy/env/hr-server.production.env.example").read_text(encoding="utf-8")
    required = {
        "MAIL_CREDENTIAL_ENCRYPTION_KEY",
        "HEALTH_CHECK_TIMEOUT_SECONDS",
        "REDIS_CONNECT_TIMEOUT_SECONDS",
        "REDIS_SOCKET_TIMEOUT_SECONDS",
        "MAIL_TASK_PROCESSING_LEASE_SECONDS",
        "MAIL_TASK_RECOVERY_INTERVAL_SECONDS",
        "MAIL_TASK_RECOVERY_BATCH_SIZE",
        "ENABLE_LOCAL_AUTH_BYPASS",
        "ENABLE_LOCAL_ADMIN_BOOTSTRAP",
    }
    assert all(f"{name}=" in content for name in required)
    assert "ACCESS_TOKEN_EXPIRE_MINUTES=15" in content
    assert "ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES=15" in content


def test_business_layer_modules_are_part_of_the_mypy_gate() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    checked_paths = set(config["tool"]["mypy"]["files"])
    expected_business_paths = {
        "src/app/application",
        "src/app/modules/payable",
        "src/app/modules/payment",
        "src/app/modules/project_timesheet_record",
        "src/app/modules/referral",
        "src/app/modules/contract_record",
        "src/app/modules/job_progress",
        "src/app/modules/talent_profile",
        "src/app/modules/job",
    }

    assert expected_business_paths <= checked_paths


def test_decomposed_business_modules_have_no_service_facades() -> None:
    for module_name in ("project_timesheet_record", "talent_profile", "contract_record", "job"):
        assert not (ROOT / "src/app/modules" / module_name / "service.py").exists()
