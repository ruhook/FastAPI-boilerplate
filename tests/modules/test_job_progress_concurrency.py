import pytest
from sqlalchemy.dialects import mysql

from src.app.core.exceptions.http_exceptions import ConflictException
from src.app.modules.job_progress.model import JobProgress
from src.app.modules.job_progress.state import (
    build_locked_job_progress_query,
    ensure_expected_progress_versions,
)

pytestmark = pytest.mark.no_database_cleanup


def test_job_progress_has_optimistic_version_column() -> None:
    column = JobProgress.__table__.c.version

    assert column.nullable is False
    assert column.default is not None
    assert column.default.arg == 1
    assert JobProgress.__mapper__.version_id_col is column


def test_locked_batch_query_orders_ids_before_for_update() -> None:
    statement = build_locked_job_progress_query(job_id=3, progress_ids=[9, 2, 5])
    sql = str(statement.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))

    assert "job_progress.id IN (2, 5, 9)" in sql
    assert "ORDER BY job_progress.id ASC" in sql
    assert sql.endswith("FOR UPDATE")


def test_expected_versions_reject_stale_batch_before_mutation() -> None:
    first = JobProgress(id=2, version=4)
    second = JobProgress(id=5, version=7)

    with pytest.raises(ConflictException, match="changed"):
        ensure_expected_progress_versions(
            [first, second],
            expected_versions={2: 4, 5: 6},
        )


def test_expected_versions_accept_matching_or_omitted_contract() -> None:
    progress = JobProgress(id=2, version=4)

    ensure_expected_progress_versions([progress], expected_versions=None)
    ensure_expected_progress_versions([progress], expected_versions={2: 4})
