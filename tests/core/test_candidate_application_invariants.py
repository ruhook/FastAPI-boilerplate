import pytest

from src.app.modules.candidate_application.model import CandidateApplication

pytestmark = pytest.mark.no_database_cleanup


def test_candidate_application_has_active_user_job_unique_index() -> None:
    indexes = {index.name: index for index in CandidateApplication.__table__.indexes}

    index = indexes["uq_candidate_application_active_user_job"]

    assert index.unique is True
    assert [column.name for column in index.columns] == ["user_id", "active_job_id"]
