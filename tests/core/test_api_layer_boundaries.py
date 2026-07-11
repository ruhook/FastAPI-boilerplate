import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_database_cleanup

API_DIR = Path(__file__).parents[2] / "src/app/api/v1"
ROUTE_MODULES = ("jobs.py", "me.py", "web_users.py", "assets.py")


@pytest.mark.parametrize("module_name", ROUTE_MODULES)
def test_web_route_modules_do_not_query_or_mutate_orm_directly(module_name: str) -> None:
    tree = ast.parse((API_DIR / module_name).read_text())

    sqlalchemy_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module in {"sqlalchemy", "sqlalchemy.orm"}
    ]
    direct_session_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "db"
        and node.func.attr in {"add", "delete", "execute", "flush", "rollback"}
    ]

    assert sqlalchemy_imports == []
    assert direct_session_calls == []
