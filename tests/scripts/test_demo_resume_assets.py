from types import SimpleNamespace

import pytest

from src.scripts import run_advanced_filter_bulk_demo, run_client_apply_demo

pytestmark = [
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.no_database_cleanup,
]


class _NoExistingAssetResult:
    def scalar_one_or_none(self):
        return None


class _FakeSession:
    def __init__(self, existing_asset=None):
        self.existing_asset = existing_asset

    async def execute(self, *_args, **_kwargs):
        if self.existing_asset is None:
            return _NoExistingAssetResult()
        return _ExistingAssetResult(self.existing_asset)

    async def commit(self):
        return None


class _ExistingAssetResult:
    def __init__(self, existing_asset):
        self.existing_asset = existing_asset

    def scalar_one_or_none(self):
        return self.existing_asset


class _FakeLocalSession:
    def __init__(self, session: _FakeSession):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


def _assert_valid_pdf(content: bytes) -> None:
    assert content.startswith(b"%PDF-")
    assert content.rstrip().endswith(b"%%EOF")


async def test_apply_demo_resume_asset_uploads_valid_pdf_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, bytes | str] = {}

    async def fake_create_asset_from_bytes(**kwargs):
        captured["original_name"] = kwargs["original_name"]
        captured["mime_type"] = kwargs["mime_type"]
        captured["content"] = kwargs["content"]
        return SimpleNamespace(id=123, original_name=kwargs["original_name"])

    monkeypatch.setattr(run_client_apply_demo, "local_session", lambda: _FakeLocalSession(_FakeSession()))
    monkeypatch.setattr(run_client_apply_demo, "create_asset_from_bytes", fake_create_asset_from_bytes)

    await run_client_apply_demo.ensure_resume_asset(user_id=1, email="candidate@example.com")

    assert captured["original_name"] == "demo-resume.pdf"
    assert captured["mime_type"] == "application/pdf"
    _assert_valid_pdf(captured["content"])


async def test_apply_demo_resume_asset_refreshes_existing_invalid_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    stored: dict[str, bytes | str] = {}
    existing_asset = SimpleNamespace(
        id=123,
        original_name="demo-resume.pdf",
        storage_key="candidate/demo-resume.pdf",
        mime_type="application/pdf",
        file_size=17,
        data={"generated_by": "old_script"},
    )

    async def fail_create_asset_from_bytes(**_kwargs):
        raise AssertionError("existing resume asset should be refreshed, not recreated")

    def fake_store_asset_content(**kwargs):
        stored["storage_key"] = kwargs["storage_key"]
        stored["mime_type"] = kwargs["mime_type"]
        stored["content"] = kwargs["content"]

    monkeypatch.setattr(run_client_apply_demo, "local_session", lambda: _FakeLocalSession(_FakeSession(existing_asset)))
    monkeypatch.setattr(run_client_apply_demo, "create_asset_from_bytes", fail_create_asset_from_bytes)
    monkeypatch.setattr(run_client_apply_demo, "read_asset_content", lambda _asset: b"not a pdf", raising=False)
    monkeypatch.setattr(run_client_apply_demo, "store_asset_content", fake_store_asset_content, raising=False)

    result = await run_client_apply_demo.ensure_resume_asset(user_id=1, email="candidate@example.com")

    assert result is existing_asset
    assert stored["storage_key"] == "candidate/demo-resume.pdf"
    assert stored["mime_type"] == "application/pdf"
    _assert_valid_pdf(stored["content"])
    assert existing_asset.file_size == len(stored["content"])


async def test_advanced_filter_demo_resume_asset_uploads_valid_pdf_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, bytes | str] = {}

    async def fake_create_asset_from_bytes(**kwargs):
        captured["original_name"] = kwargs["original_name"]
        captured["mime_type"] = kwargs["mime_type"]
        captured["content"] = kwargs["content"]
        return SimpleNamespace(id=456, original_name=kwargs["original_name"])

    monkeypatch.setattr(run_advanced_filter_bulk_demo, "create_asset_from_bytes", fake_create_asset_from_bytes)

    await run_advanced_filter_bulk_demo.ensure_candidate_resume_asset(
        _FakeSession(),
        user_id=1,
        email="candidate@example.com",
    )

    assert captured["original_name"] == "demo-resume.pdf"
    assert captured["mime_type"] == "application/pdf"
    _assert_valid_pdf(captured["content"])


async def test_advanced_filter_demo_resume_asset_refreshes_existing_invalid_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: dict[str, bytes | str] = {}
    existing_asset = SimpleNamespace(
        id=456,
        original_name="demo-resume.pdf",
        storage_key="candidate/bulk-demo-resume.pdf",
        mime_type="application/pdf",
        file_size=23,
        data={"generated_by": "old_bulk_script"},
    )

    async def fail_create_asset_from_bytes(**_kwargs):
        raise AssertionError("existing resume asset should be refreshed, not recreated")

    def fake_store_asset_content(**kwargs):
        stored["storage_key"] = kwargs["storage_key"]
        stored["mime_type"] = kwargs["mime_type"]
        stored["content"] = kwargs["content"]

    monkeypatch.setattr(run_advanced_filter_bulk_demo, "create_asset_from_bytes", fail_create_asset_from_bytes)
    monkeypatch.setattr(run_advanced_filter_bulk_demo, "read_asset_content", lambda _asset: b"not a pdf", raising=False)
    monkeypatch.setattr(run_advanced_filter_bulk_demo, "store_asset_content", fake_store_asset_content, raising=False)

    result = await run_advanced_filter_bulk_demo.ensure_candidate_resume_asset(
        _FakeSession(existing_asset),
        user_id=1,
        email="candidate@example.com",
    )

    assert result is existing_asset
    assert stored["storage_key"] == "candidate/bulk-demo-resume.pdf"
    assert stored["mime_type"] == "application/pdf"
    _assert_valid_pdf(stored["content"])
    assert existing_asset.file_size == len(stored["content"])
