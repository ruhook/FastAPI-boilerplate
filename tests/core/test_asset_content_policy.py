import pytest

from src.app.core.exceptions.http_exceptions import BadRequestException
from src.app.modules.assets.content_policy import classify_asset_content
from src.app.modules.assets.model import Asset
from src.app.modules.assets.responses import build_asset_response

pytestmark = pytest.mark.no_database_cleanup

PNG = b"\x89PNG\r\n\x1a\n" + b"safe-raster"


def test_client_mime_cannot_turn_html_into_an_image() -> None:
    with pytest.raises(BadRequestException, match="Unsupported or mismatched"):
        classify_asset_content("avatar.png", b"<!doctype html><script>alert(1)</script>")


@pytest.mark.parametrize("filename", ["payload.html", "payload.svg", "payload.js", "payload.xml"])
def test_active_content_extensions_are_rejected(filename: str) -> None:
    with pytest.raises(BadRequestException, match="not supported"):
        classify_asset_content(filename, b"<svg onload='alert(1)'></svg>")


def test_raster_image_uses_server_owned_mime_and_inline_policy() -> None:
    policy = classify_asset_content("avatar.png", PNG)

    assert policy.mime_type == "image/png"
    assert policy.suffix == "png"
    assert policy.inline_preview is True


def test_pdf_is_recognized_but_never_inline() -> None:
    policy = classify_asset_content("resume.pdf", b"%PDF-1.7\nbody")

    assert policy.mime_type == "application/pdf"
    assert policy.suffix == "pdf"
    assert policy.inline_preview is False


def test_extension_and_signature_must_agree() -> None:
    with pytest.raises(BadRequestException, match="Unsupported or mismatched"):
        classify_asset_content("resume.pdf", PNG)


def test_raster_preview_is_inline_with_active_content_defenses() -> None:
    asset = Asset(
        id=1,
        type="image",
        module="rich_text",
        original_name="avatar.png",
        storage_key="private/avatar.png",
        mime_type="text/html",
        file_size=len(PNG),
        data={},
    )

    response = build_asset_response(asset, PNG, preview=True)

    assert response.media_type == "image/png"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"


def test_pdf_preview_is_forced_to_attachment() -> None:
    content = b"%PDF-1.7\nbody"
    asset = Asset(
        id=2,
        type="file",
        module="candidate_application",
        original_name="resume.pdf",
        storage_key="private/resume.pdf",
        mime_type="text/html",
        file_size=len(content),
        data={},
    )

    response = build_asset_response(asset, content, preview=True)

    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
