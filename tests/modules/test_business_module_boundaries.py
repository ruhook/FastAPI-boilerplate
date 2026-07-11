import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_database_cleanup

MODULE_ROOT = Path(__file__).parents[2] / "src/app/modules"


def _relative_imports(module_dir: str, module_name: str) -> set[str]:
    tree = ast.parse((MODULE_ROOT / module_dir / f"{module_name}.py").read_text())
    return {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level > 0
    }


def test_contract_module_has_explicit_command_query_projection_boundaries() -> None:
    module_dir = MODULE_ROOT / "contract_record"

    assert not (module_dir / "service.py").exists()
    assert {"commands.py", "queries.py", "serialization.py", "policy.py"} <= {
        path.name for path in module_dir.glob("*.py")
    }
    assert "commands" not in _relative_imports("contract_record", "queries")
    assert "commands" not in _relative_imports("contract_record", "serialization")
    assert "queries" not in _relative_imports("contract_record", "serialization")


def test_job_module_has_explicit_command_query_policy_projection_boundaries() -> None:
    module_dir = MODULE_ROOT / "job"

    assert not (module_dir / "service.py").exists()
    assert {"commands.py", "queries.py", "serialization.py", "policy.py"} <= {
        path.name for path in module_dir.glob("*.py")
    }
    assert "commands" not in _relative_imports("job", "queries")
    assert "commands" not in _relative_imports("job", "serialization")
    assert "queries" not in _relative_imports("job", "serialization")
    assert "commands" not in _relative_imports("job", "policy")
    assert "queries" not in _relative_imports("job", "policy")
