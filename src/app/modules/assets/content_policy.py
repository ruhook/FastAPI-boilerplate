from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import NoReturn
from zipfile import BadZipFile, ZipFile

from ...core.exceptions.http_exceptions import BadRequestException


@dataclass(frozen=True, slots=True)
class AssetContentPolicy:
    mime_type: str
    suffix: str
    inline_preview: bool


ACTIVE_SUFFIXES = {".html", ".htm", ".xhtml", ".svg", ".xml", ".js", ".mjs"}
RASTER_TYPES: dict[str, tuple[str, Callable[[bytes], bool]]] = {
    ".jpg": ("image/jpeg", lambda value: value.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda value: value.startswith(b"\xff\xd8\xff")),
    ".png": ("image/png", lambda value: value.startswith(b"\x89PNG\r\n\x1a\n")),
    ".gif": ("image/gif", lambda value: value.startswith((b"GIF87a", b"GIF89a"))),
    ".webp": (
        "image/webp",
        lambda value: len(value) >= 12 and value[:4] == b"RIFF" and value[8:12] == b"WEBP",
    ),
}
OOXML_TYPES = {
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "word/",
    ),
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xl/",
    ),
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "ppt/",
    ),
}
OLE_TYPES = {
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
    ".ppt": "application/vnd.ms-powerpoint",
}
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
MAX_ARCHIVE_MEMBERS = 10_000


def _reject(message: str) -> NoReturn:
    raise BadRequestException(message)


def _classify_zip(suffix: str, content: bytes) -> AssetContentPolicy:
    try:
        with ZipFile(BytesIO(content)) as archive:
            names = archive.namelist()
    except BadZipFile:
        _reject("Unsupported or mismatched asset content.")

    if len(names) > MAX_ARCHIVE_MEMBERS:
        _reject("Archive contains too many entries.")
    if suffix == ".zip":
        return AssetContentPolicy("application/zip", "zip", False)

    mime_type, required_prefix = OOXML_TYPES[suffix]
    if "[Content_Types].xml" not in names or not any(name.startswith(required_prefix) for name in names):
        _reject("Unsupported or mismatched asset content.")
    return AssetContentPolicy(mime_type, suffix[1:], False)


def classify_asset_content(filename: str, content: bytes) -> AssetContentPolicy:
    suffix = Path(filename).suffix.lower()
    if suffix in ACTIVE_SUFFIXES:
        _reject("This file type is not supported.")

    probe = content[:1024].lstrip().lower()
    if probe.startswith((b"<!doctype html", b"<html", b"<svg", b"<?xml")):
        _reject("Unsupported or mismatched asset content.")

    if suffix in RASTER_TYPES:
        mime_type, matches = RASTER_TYPES[suffix]
        if matches(content):
            return AssetContentPolicy(mime_type, suffix[1:], True)
    elif suffix == ".pdf" and content.startswith(b"%PDF-"):
        return AssetContentPolicy("application/pdf", "pdf", False)
    elif suffix in {*OOXML_TYPES, ".zip"} and content.startswith(b"PK"):
        return _classify_zip(suffix, content)
    elif suffix in OLE_TYPES and content.startswith(OLE_MAGIC):
        return AssetContentPolicy(OLE_TYPES[suffix], suffix[1:], False)
    elif suffix == ".txt" and b"\x00" not in content:
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            return AssetContentPolicy("text/plain; charset=utf-8", "txt", False)

    _reject("Unsupported or mismatched asset content.")
