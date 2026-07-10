from urllib.parse import quote

from fastapi.responses import Response

from .content_policy import classify_asset_content
from .model import Asset


def build_content_disposition(disposition: str, filename: str) -> str:
    safe_filename = "".join(
        char if 32 <= ord(char) < 127 and char not in {'"', "\\"} else "_" for char in filename
    )
    safe_filename = safe_filename or "download"
    encoded_filename = quote(filename, safe="")
    return f'{disposition}; filename="{safe_filename}"; filename*=UTF-8\'\'{encoded_filename}'


def build_security_headers(disposition: str, filename: str) -> dict[str, str]:
    return {
        "Content-Disposition": build_content_disposition(disposition, filename),
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }


def build_asset_response(asset: Asset, content: bytes, *, preview: bool) -> Response:
    policy = classify_asset_content(asset.original_name, content)
    disposition = "inline" if preview and policy.inline_preview else "attachment"
    return Response(
        content=content,
        media_type=policy.mime_type,
        headers=build_security_headers(disposition, asset.original_name),
    )


def build_download_response(content: bytes, *, media_type: str, filename: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers=build_security_headers("attachment", filename),
    )
