import pytest

from src.app.modules.job.schema import JobFormField
from src.scripts import seed_preview_demo_data

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.no_database_cleanup,
]


class _NoTemplateResult:
    def scalar_one_or_none(self):
        return None


class _FakeSession:
    def __init__(self):
        self.added = []

    async def execute(self, *_args, **_kwargs):
        return _NoTemplateResult()

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        return None


async def test_preview_form_template_fields_are_job_read_compatible() -> None:
    template = await seed_preview_demo_data._ensure_form_template(_FakeSession())

    assert template.fields
    for field in template.fields:
        assert "required" in field
        assert "canFilter" in field
        JobFormField.model_validate(field)
