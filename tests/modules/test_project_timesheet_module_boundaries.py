import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_database_cleanup

MODULE_DIR = Path(__file__).parents[2] / "src/app/modules/project_timesheet_record"


def _relative_imports(module_name: str) -> set[str]:
    tree = ast.parse((MODULE_DIR / f"{module_name}.py").read_text())
    return {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level > 0
    }


def test_timesheet_module_has_explicit_read_write_and_projection_boundaries() -> None:
    assert not (MODULE_DIR / "service.py").exists()
    assert {"commands.py", "queries.py", "analytics.py", "serialization.py"} <= {
        path.name for path in MODULE_DIR.glob("*.py")
    }

    assert "commands" not in _relative_imports("queries")
    assert "commands" not in _relative_imports("analytics")
    assert "analytics" not in _relative_imports("commands")
