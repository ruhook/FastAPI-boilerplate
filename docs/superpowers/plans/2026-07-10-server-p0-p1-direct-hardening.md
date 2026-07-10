# Server P0/P1 Direct Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Directly replace the remaining unsafe asset, application-concurrency, password, authentication-abuse, and event-consumer mechanisms without backward-compatibility shims.

**Architecture:** Keep the FastAPI modular monolith, MySQL, Redis, transactional outbox, and local Admin bypass. Add focused foundation modules for asset content policy and authentication rate limits, enforce the application invariant in MySQL, replace bcrypt with Argon2id, and make the Redis Stream consumer a bounded worker pool with a dead-letter stream.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2 async, Alembic, MySQL 8, Redis Streams, redis-py asyncio, argon2-cffi, pytest/pytest-asyncio, Ruff, mypy, uv.

## Global Constraints

- Do not preserve bcrypt verification, client-declared MIME fallback, old Redis pending messages, deprecated setting aliases, or old enumeration-prone response text.
- Do not silently delete or select conflicting database records in migrations; a dirty development database must fail visibly and be reset or repaired.
- Every authenticated Admin account can read every Admin-visible asset, including candidate, process, company, timesheet, and mail assets created by another Admin.
- Candidate asset access remains ownership/reference based.
- Preserve the explicit local-only virtual Admin bypass.
- Do not add a new infrastructure service.
- Write and run a failing regression test before each production behavior change.
- Do not run the destructive full database suite against the user's current `hr_server` database; use a disposable allowlisted database or leave that gate to CI.

---

## File Structure

### New focused modules

- `src/app/modules/assets/content_policy.py`: byte/extension classification and server-owned delivery policy.
- `src/app/modules/assets/responses.py`: shared safe `Content-Disposition`, `nosniff`, CSP, and attachment/inline response creation.
- `src/app/core/auth_rate_limit.py`: domain-separated HMAC keys and atomic Redis rate-limit enforcement.
- `tests/core/test_asset_content_policy.py`: pure classification and delivery-policy tests.
- `tests/core/test_password_hashing.py`: Argon2-only and dummy-verification tests.
- `tests/core/test_auth_rate_limit.py`: pure/fake-Redis limiter tests.
- `tests/web/test_auth_abuse.py`: public-response and route-wiring tests.
- `tests/web/test_job_application_concurrency.py`: real MySQL concurrency invariants.
- `src/migrations/versions/20260710_000045_active_candidate_application.py`: generated active-job column and unique index.

### Existing files changed

- `src/app/modules/assets/service.py`: classify bytes before storage and persist only detected MIME.
- `src/app/api/v1/assets.py`: use shared safe responses.
- `src/app/admin/api/v1/settings/assets.py`: make Admin asset visibility intentionally global and use shared safe responses.
- `src/app/modules/candidate_application/model.py`: map the generated active-job column and unique index.
- `src/app/modules/talent_profile/service.py`: translate only the active-application constraint and atomically update job counters.
- `src/app/core/security.py`: replace bcrypt with Argon2id and equalize missing/disabled account verification.
- `src/app/core/config.py`: add and validate limiter, worker, DLQ, and password input settings.
- `src/app/core/exceptions/http_exceptions.py`: add an HTTP 429 exception with `Retry-After`.
- `src/app/api/v1/login.py`: apply Web login rate limits.
- `src/app/admin/api/v1/auth.py`: apply Admin login rate limits except the explicit local bypass.
- `src/app/api/v1/web_users.py`: pass request identity and return uniform verification-send responses.
- `src/app/modules/user/register_verification_service.py`: apply send/check limits, remove enumeration, and domain-separate code HMAC.
- `src/app/event/mq_client.py`: bounded workers, fair fetch, delivery counts, and atomic DLQ transfer.
- `src/app/event/event_manager.py`: remove the ineffective second concurrency layer.
- `event_consumer.py`: let `AsyncMQClient` own worker concurrency.
- `src/.env.example`: document all new settings.
- `pyproject.toml`, `uv.lock`: remove bcrypt and add Argon2.
- `docs/server-foundation-hardening-status-zh.md`: record the completed P0/P1 tranche and remaining P2 debt.

---

### Task 1: Safe asset classification, delivery, and intentional Admin-wide visibility

**Files:**

- Create: `src/app/modules/assets/content_policy.py`
- Create: `src/app/modules/assets/responses.py`
- Create: `tests/core/test_asset_content_policy.py`
- Modify: `src/app/modules/assets/service.py:1-290`
- Modify: `src/app/api/v1/assets.py:1-195`
- Modify: `src/app/admin/api/v1/settings/assets.py:1-350`
- Modify: `tests/core/test_asset_safety.py:65-95`

**Interfaces:**

- Produces: `classify_asset_content(filename: str, content: bytes) -> AssetContentPolicy`.
- Produces: `build_asset_response(asset: Asset, content: bytes, *, preview: bool) -> Response`.
- Produces: `build_download_response(content: bytes, *, media_type: str, filename: str) -> Response`.
- Preserves: candidate ownership/reference checks.
- Changes: `current_admin_can_access_asset(...)` becomes true for every authenticated Admin identity.

- [ ] **Step 1: Write failing content-policy and Admin-visibility tests**

Create `tests/core/test_asset_content_policy.py` with tests equivalent to:

```python
import pytest

from src.app.core.exceptions.http_exceptions import BadRequestException
from src.app.modules.assets.content_policy import classify_asset_content


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
    assert policy.inline_preview is True


def test_pdf_is_recognized_but_never_inline() -> None:
    policy = classify_asset_content("resume.pdf", b"%PDF-1.7\nbody")
    assert policy.mime_type == "application/pdf"
    assert policy.inline_preview is False
```

Change the existing mail-asset test in `tests/core/test_asset_safety.py` to express the approved sharing rule:

```python
@pytest.mark.asyncio
async def test_authenticated_admin_can_access_asset_created_by_another_admin() -> None:
    allowed = await admin_assets_api.current_admin_can_access_asset(
        SimpleNamespace(),
        asset={"id": 9, "module": "mail", "owner_type": "admin_user", "owner_id": 7},
        current_admin={"id": 8, "is_superuser": False, "permissions": []},
    )
    assert allowed is True
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
uv run pytest -q tests/core/test_asset_content_policy.py tests/core/test_asset_safety.py
```

Expected: collection fails because `content_policy` does not exist, and the existing Admin mail test fails under the new expectation once collection is available.

- [ ] **Step 3: Implement byte/extension classification**

Create `src/app/modules/assets/content_policy.py` around this complete public contract:

```python
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
RASTER_TYPES = {
    ".jpg": ("image/jpeg", lambda value: value.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda value: value.startswith(b"\xff\xd8\xff")),
    ".png": ("image/png", lambda value: value.startswith(b"\x89PNG\r\n\x1a\n")),
    ".gif": ("image/gif", lambda value: value.startswith((b"GIF87a", b"GIF89a"))),
    ".webp": ("image/webp", lambda value: len(value) >= 12 and value[:4] == b"RIFF" and value[8:12] == b"WEBP"),
}
OOXML_TYPES = {
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "word/"),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xl/"),
    ".pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "ppt/"),
}
OLE_TYPES = {
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
    ".ppt": "application/vnd.ms-powerpoint",
}
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _reject(message: str) -> NoReturn:
    raise BadRequestException(message)


def _classify_zip(suffix: str, content: bytes) -> AssetContentPolicy:
    try:
        with ZipFile(BytesIO(content)) as archive:
            names = archive.namelist()
    except BadZipFile:
        _reject("Unsupported or mismatched asset content.")
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
```

The implementation may split private signature helpers, but it must keep the public dataclass/function names and behavior above.

- [ ] **Step 4: Make storage persist only detected metadata**

In `upload_asset`, use the server-owned result and ignore `UploadFile.content_type`:

```python
policy = classify_asset_content(filename, content)
storage_key = _build_asset_storage_key(payload=payload, original_name=filename)
store_asset_content(storage_key=storage_key, content=content, mime_type=policy.mime_type)

asset = Asset(
    type=payload.type,
    module=payload.module,
    owner_type=payload.owner_type,
    owner_id=payload.owner_id,
    original_name=filename,
    storage_key=storage_key,
    mime_type=policy.mime_type,
    file_size=len(content),
    data={"suffix": policy.suffix},
)
```

In `create_asset_from_bytes`, treat the optional supplied MIME as an assertion:

```python
policy = classify_asset_content(resolved_name, content)
if mime_type is not None and mime_type.split(";", 1)[0].strip().lower() != policy.mime_type.split(";", 1)[0]:
    raise BadRequestException("Declared asset media type does not match its content.")
storage_key = _build_asset_storage_key(payload=payload, original_name=resolved_name)
store_asset_content(storage_key=storage_key, content=content, mime_type=policy.mime_type)
asset_data = {"suffix": policy.suffix}
if data:
    asset_data.update(data)

asset = Asset(
    type=payload.type,
    module=payload.module,
    owner_type=payload.owner_type,
    owner_id=payload.owner_id,
    original_name=resolved_name,
    storage_key=storage_key,
    mime_type=policy.mime_type,
    file_size=len(content),
    data=asset_data,
)
```

Remove `mimetypes` from `service.py`. Ensure classification happens before storage so rejected content creates neither an object nor a database row.

- [ ] **Step 5: Centralize safe responses and Admin sharing**

Create `src/app/modules/assets/responses.py` with:

```python
from urllib.parse import quote

from fastapi.responses import Response

from .content_policy import classify_asset_content
from .model import Asset


def build_content_disposition(disposition: str, filename: str) -> str:
    safe = "".join(char if 32 <= ord(char) < 127 and char not in {'"', "\\"} else "_" for char in filename)
    safe = safe or "download"
    return f"{disposition}; filename=\"{safe}\"; filename*=UTF-8''{quote(filename, safe='')}"


def _security_headers(disposition: str, filename: str) -> dict[str, str]:
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
        headers=_security_headers(disposition, asset.original_name),
    )


def build_download_response(content: bytes, *, media_type: str, filename: str) -> Response:
    return Response(content=content, media_type=media_type, headers=_security_headers("attachment", filename))
```

Replace duplicated preview/download construction in both routers with these helpers. Add `nosniff` to batch ZIP and PDF export responses.

Replace the Admin access function with the explicit shared-data rule:

```python
async def current_admin_can_access_asset(db, *, asset, current_admin) -> bool:
    _ = (db, asset)
    return current_admin.get("id") is not None
```

Delete the reviewer-only asset SQL and its unused imports; authentication remains enforced by `get_current_admin_user` on every Admin route.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run:

```bash
uv run pytest -q tests/core/test_asset_content_policy.py tests/core/test_asset_safety.py tests/admin/test_asset_preview_routes.py
uv run ruff check --no-fix src/app/modules/assets src/app/api/v1/assets.py src/app/admin/api/v1/settings/assets.py tests/core/test_asset_content_policy.py tests/core/test_asset_safety.py
```

Expected: all focused tests pass and Ruff exits 0.

- [ ] **Step 7: Commit the asset boundary**

```bash
git add src/app/modules/assets/content_policy.py src/app/modules/assets/responses.py src/app/modules/assets/service.py src/app/api/v1/assets.py src/app/admin/api/v1/settings/assets.py tests/core/test_asset_content_policy.py tests/core/test_asset_safety.py tests/admin/test_asset_preview_routes.py
git commit -m "fix: enforce safe asset content delivery"
```

---

### Task 2: Database-enforced application uniqueness and atomic counters

**Files:**

- Create: `src/migrations/versions/20260710_000045_active_candidate_application.py`
- Create: `tests/web/test_job_application_concurrency.py`
- Modify: `src/app/modules/candidate_application/model.py:1-30`
- Modify: `src/app/modules/talent_profile/service.py:1-40,969-1020`
- Modify: `tests/web/test_job_application_errors.py:105-170`

**Interfaces:**

- Produces: generated `CandidateApplication.active_job_id: int | None`.
- Produces: unique index `uq_candidate_application_active_user_job` on `(user_id, active_job_id)`.
- Preserves: public duplicate response `400` with `You have already applied to this role.`.
- Changes: job count and JSON summary applicant count update in one SQL statement.

- [ ] **Step 1: Write failing model and concurrency tests**

Add a pure metadata assertion:

```python
def test_candidate_application_has_active_user_job_unique_index() -> None:
    indexes = {index.name: index for index in CandidateApplication.__table__.indexes}
    index = indexes["uq_candidate_application_active_user_job"]
    assert index.unique is True
    assert [column.name for column in index.columns] == ["user_id", "active_job_id"]
```

Create `tests/web/test_job_application_concurrency.py` with the existing talent helpers and the following test shape:

```python
import asyncio
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.core.db.database import local_session
from src.app.modules.candidate_application.model import CandidateApplication
from src.app.modules.job.model import Job
from tests.helpers.talent import (
    build_application_items,
    build_form_fields,
    create_candidate_user,
    create_form_template,
    create_open_job,
    create_resume_asset,
    login_web_user,
)

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _items(name: str, email: str, resume_asset_id: int) -> dict[str, object]:
    return {
        "items": build_application_items(
            full_name=name,
            email=email,
            whatsapp="+1-555-0100",
            nationality="Chinese",
            country_of_residence="China",
            education_status="Bachelor’s degree (completed)",
            resume_asset_id=resume_asset_id,
        )
    }


async def _seed_job(db: AsyncSession, owner_id: int, suffix: str) -> Job:
    fields = build_form_fields()
    for field in fields:
        if field.get("key") == "resume_attachment":
            field["type"] = "file"
    template = await create_form_template(db, suffix=suffix, fields=fields)
    return await create_open_job(
        db,
        suffix=suffix,
        title=f"Concurrent Role {suffix}",
        owner_admin_user_id=owner_id,
        form_template_id=template.id,
        form_fields=fields,
    )


async def test_same_candidate_concurrent_apply_creates_one_active_row(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    job = await _seed_job(db_session, int(superadmin_credentials["id"]), suffix)
    user, password = await create_candidate_user(db_session, suffix=suffix, name="Concurrent Candidate")
    headers = await login_web_user(web_client, username=user.email, password=password)
    resume = await create_resume_asset(db_session, suffix=suffix, original_name="resume.pdf")
    resume.owner_id = user.id
    resume.module = "candidate_application"
    await db_session.commit()
    payload = _items(user.name, user.email, resume.id)

    responses = await asyncio.gather(
        web_client.post(f"/api/v1/jobs/{job.id}/apply", headers=headers, json=payload),
        web_client.post(f"/api/v1/jobs/{job.id}/apply", headers=headers, json=payload),
    )
    assert sorted(response.status_code for response in responses) == [200, 400]

    async with local_session() as assertion_db:
        count = await assertion_db.scalar(
            select(func.count(CandidateApplication.id)).where(
                CandidateApplication.user_id == user.id,
                CandidateApplication.job_id == job.id,
                CandidateApplication.is_deleted.is_(False),
            )
        )
        stored_job = await assertion_db.get(Job, job.id)
        assert count == 1
        assert stored_job is not None and stored_job.applicant_count == 1
```

Add the different-user test:

```python
async def test_different_candidates_concurrent_apply_preserves_both_count_increments(
    web_client: AsyncClient,
    db_session: AsyncSession,
    superadmin_credentials: dict[str, str | int],
) -> None:
    suffix = uuid4().hex[:8]
    job = await _seed_job(db_session, int(superadmin_credentials["id"]), suffix)
    first, first_password = await create_candidate_user(db_session, suffix=f"{suffix}a", name="First Candidate")
    second, second_password = await create_candidate_user(db_session, suffix=f"{suffix}b", name="Second Candidate")
    first_headers = await login_web_user(web_client, username=first.email, password=first_password)
    second_headers = await login_web_user(web_client, username=second.email, password=second_password)
    first_resume = await create_resume_asset(db_session, suffix=f"{suffix}a", original_name="first.pdf")
    second_resume = await create_resume_asset(db_session, suffix=f"{suffix}b", original_name="second.pdf")
    first_resume.owner_id = first.id
    second_resume.owner_id = second.id
    first_resume.module = second_resume.module = "candidate_application"
    await db_session.commit()

    responses = await asyncio.gather(
        web_client.post(
            f"/api/v1/jobs/{job.id}/apply",
            headers=first_headers,
            json=_items(first.name, first.email, first_resume.id),
        ),
        web_client.post(
            f"/api/v1/jobs/{job.id}/apply",
            headers=second_headers,
            json=_items(second.name, second.email, second_resume.id),
        ),
    )
    assert [response.status_code for response in responses] == [200, 200]

    async with local_session() as assertion_db:
        count = await assertion_db.scalar(
            select(func.count(CandidateApplication.id)).where(
                CandidateApplication.job_id == job.id,
                CandidateApplication.is_deleted.is_(False),
            )
        )
        stored_job = await assertion_db.get(Job, job.id)
        assert count == 2
        assert stored_job is not None and stored_job.applicant_count == 2
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest -q tests/web/test_job_application_errors.py tests/web/test_job_application_concurrency.py
```

Expected: the metadata test fails because `active_job_id` and its unique index do not exist; the same-user race may create two rows or return two successful responses.

- [ ] **Step 3: Add the generated column and migration**

Map the model using:

```python
from sqlalchemy import Computed, DateTime, ForeignKey, Index, Integer, String, text

class CandidateApplication(DataBackedSoftDeleteEntityMixin, Base):
    __tablename__ = "candidate_application"
    __table_args__ = (
        Index("uq_candidate_application_active_user_job", "user_id", "active_job_id", unique=True),
    )

    user_id: Mapped[int] = mapped_column(ForeignKey("user.id"), nullable=False, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("job.id"), nullable=False, index=True)
    active_job_id: Mapped[int | None] = mapped_column(
        Integer,
        Computed("CASE WHEN is_deleted = 0 THEN job_id ELSE NULL END", persisted=True),
        nullable=True,
    )
```

Create revision `20260710_000045`, down revision `20260710_000044`:

```python
def upgrade() -> None:
    op.add_column(
        "candidate_application",
        sa.Column(
            "active_job_id",
            sa.Integer(),
            sa.Computed("CASE WHEN is_deleted = 0 THEN job_id ELSE NULL END", persisted=True),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_candidate_application_active_user_job",
        "candidate_application",
        ["user_id", "active_job_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_candidate_application_active_user_job", table_name="candidate_application")
    op.drop_column("candidate_application", "active_job_id")
```

- [ ] **Step 4: Translate only the expected unique conflict**

Import `IntegrityError` and wrap the first application flush:

```python
try:
    await db.flush()
except IntegrityError as exc:
    if "uq_candidate_application_active_user_job" in str(exc.orig):
        raise BadRequestException("You have already applied to this role.") from None
    raise
```

Do not catch every `IntegrityError` as a duplicate application.

- [ ] **Step 5: Atomically update explicit and JSON counts**

Replace the Python read-modify-write block with one MySQL update and refresh:

```python
next_count = Job.applicant_count + 1
summary_path = f"$.{JOB_DATA_APPLICATION_SUMMARY_KEY}.applicants"
summary_object_path = f"$.{JOB_DATA_APPLICATION_SUMMARY_KEY}"
next_data = case(
    (func.json_type(func.json_extract(Job.data, summary_object_path)) == "OBJECT",
     func.json_set(Job.data, summary_path, next_count)),
    else_=Job.data,
)
await db.execute(
    update(Job)
    .where(Job.id == job.id)
    .values(applicant_count=next_count, data=next_data)
    .execution_options(synchronize_session=False)
)
await db.refresh(job, attribute_names=["applicant_count", "data"])
```

Add `update` to the SQLAlchemy imports and remove the old dictionary mutation.

- [ ] **Step 6: Apply migration only to a disposable database, then verify GREEN**

Run against a dedicated allowlisted test database:

```bash
uv run alembic -c src/alembic.ini heads
uv run alembic -c src/alembic.ini upgrade head
uv run pytest -q tests/web/test_job_application_errors.py tests/web/test_job_application_concurrency.py tests/web/test_job_applications.py
uv run ruff check --no-fix src/app/modules/candidate_application/model.py src/app/modules/talent_profile/service.py src/migrations/versions/20260710_000045_active_candidate_application.py tests/web/test_job_application_concurrency.py
```

Expected: one Alembic head (`20260710_000045`); focused application tests pass. If no disposable database is configured, run the metadata/unit portion and leave the destructive integration command to CI rather than touching the user's current database.

- [ ] **Step 7: Commit the application invariant**

```bash
git add src/app/modules/candidate_application/model.py src/app/modules/talent_profile/service.py src/migrations/versions/20260710_000045_active_candidate_application.py tests/web/test_job_application_errors.py tests/web/test_job_application_concurrency.py
git commit -m "fix: enforce unique concurrent applications"
```

---

### Task 3: Direct Argon2id password replacement

**Files:**

- Create: `tests/core/test_password_hashing.py`
- Modify: `src/app/core/security.py:1-75`
- Modify: `pyproject.toml:8-35`
- Modify: `uv.lock`
- Modify: `tests/web/test_auth.py`
- Modify: `tests/admin/test_auth.py`

**Interfaces:**

- Preserves: `get_password_hash(password: str) -> str` and async `verify_password(...) -> bool` call shapes.
- Changes: only `$argon2id$` hashes verify; bcrypt strings return false.
- Produces: one process-initialized `DUMMY_PASSWORD_HASH` and a 512-byte password-input ceiling.

- [ ] **Step 1: Write failing Argon2-only tests**

Create `tests/core/test_password_hashing.py`:

```python
import pytest

from src.app.core import security

pytestmark = pytest.mark.no_database_cleanup


@pytest.mark.asyncio
async def test_argon2_hash_checks_every_byte_after_bcrypts_old_boundary() -> None:
    first = "a" * 72 + "x"
    second = "a" * 72 + "y"
    hashed = security.get_password_hash(first)
    assert hashed.startswith("$argon2id$")
    assert await security.verify_password(first, hashed) is True
    assert await security.verify_password(second, hashed) is False


@pytest.mark.asyncio
async def test_bcrypt_hash_is_rejected_without_fallback() -> None:
    legacy = "$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6Ttx4A0hU8JQj1U0N3Q0L7z2R3y1K"
    assert await security.verify_password("password", legacy) is False


def test_hashing_rejects_password_input_over_512_utf8_bytes() -> None:
    with pytest.raises(ValueError, match="512 UTF-8 bytes"):
        security.get_password_hash("界" * 171)
```

Add monkeypatch tests proving `authenticate_user` and `authenticate_admin_user` call `verify_password` once with `DUMMY_PASSWORD_HASH` for missing users and disabled Admins.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest -q tests/core/test_password_hashing.py
```

Expected: Argon2 prefix assertion fails under bcrypt, the byte-73 distinction fails, and the dummy hash symbol is absent.

- [ ] **Step 3: Replace the dependency and lockfile**

In `pyproject.toml`, replace `bcrypt>=4.1.1` with:

```toml
"argon2-cffi>=23.1.0,<26",
```

Then run:

```bash
uv lock
uv sync --all-extras
```

The resulting `uv.lock` must contain `argon2-cffi` and no direct project dependency on bcrypt.

- [ ] **Step 4: Implement Argon2id and dummy verification**

Replace bcrypt helpers in `security.py` with:

```python
import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

PASSWORD_MAX_UTF8_BYTES = 512
PASSWORD_HASHER = PasswordHasher()
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("server-dummy-password-value")


def _validate_password_input(password: str) -> None:
    if len(password.encode("utf-8")) > PASSWORD_MAX_UTF8_BYTES:
        raise ValueError("Password must not exceed 512 UTF-8 bytes.")


async def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        _validate_password_input(plain_password)
        return bool(await asyncio.to_thread(PASSWORD_HASHER.verify, hashed_password, plain_password))
    except (InvalidHashError, VerificationError, VerifyMismatchError, ValueError):
        return False


def get_password_hash(password: str) -> str:
    _validate_password_input(password)
    return PASSWORD_HASHER.hash(password)
```

Restructure authentication so one expensive verification always occurs:

```python
candidate_hash = db_user["hashed_password"] if db_user else DUMMY_PASSWORD_HASH
verified = await verify_password(password, candidate_hash)
if not db_user or not verified:
    return False
```

For Admins, use the real hash only for an enabled account; missing or disabled accounts verify against the dummy hash and then return false.

- [ ] **Step 5: Verify password flows GREEN**

Run:

```bash
uv run pytest -q tests/core/test_password_hashing.py tests/web/test_auth.py tests/admin/test_auth.py tests/core/test_auth_invalidation.py
uv run ruff check --no-fix src/app/core/security.py tests/core/test_password_hashing.py
uv run mypy src/app/core/security.py
```

Expected: all focused password/session flows pass; Ruff and mypy exit 0.

- [ ] **Step 6: Commit the direct password replacement**

```bash
git add pyproject.toml uv.lock src/app/core/security.py tests/core/test_password_hashing.py tests/web/test_auth.py tests/admin/test_auth.py
git commit -m "fix: replace bcrypt with argon2id"
```

---

### Task 4: Redis authentication abuse controls and non-enumerating verification

**Files:**

- Create: `src/app/core/auth_rate_limit.py`
- Create: `tests/core/test_auth_rate_limit.py`
- Create: `tests/web/test_auth_abuse.py`
- Modify: `src/app/core/config.py:140-205,270-330`
- Modify: `src/app/core/exceptions/http_exceptions.py`
- Modify: `src/app/api/v1/login.py:1-55`
- Modify: `src/app/admin/api/v1/auth.py:1-45`
- Modify: `src/app/api/v1/web_users.py:1-220`
- Modify: `src/app/modules/user/register_verification_service.py:1-370`
- Modify: `src/.env.example:30-60`

**Interfaces:**

- Produces: `AuthRateLimitAction` and `enforce_auth_rate_limit(redis, *, action, client_ip, identifier)`.
- Produces: `TooManyRequestsException(detail, retry_after)` with HTTP 429 and `Retry-After`.
- Produces: `AuthRateLimitUnavailableException` with HTTP 503 outside local development when Redis cannot enforce the boundary.
- Changes: registration/reset send routes always return the same accepted public message.
- Changes: verification-code HMAC is derived from `SECRET_KEY` with a domain label, never the SMTP password.

- [ ] **Step 1: Write failing limiter, enumeration, and secret-separation tests**

In `tests/core/test_auth_rate_limit.py`, use a fake Redis whose `eval` implements increment/expiry semantics. Assert:

```python
@pytest.mark.asyncio
async def test_login_pair_limit_returns_retry_after(monkeypatch) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(settings, "AUTH_LOGIN_PAIR_LIMIT", 1)
    await enforce_auth_rate_limit(redis, action=AuthRateLimitAction.LOGIN, client_ip="127.0.0.1", identifier="User@Example.com")
    with pytest.raises(TooManyRequestsException) as caught:
        await enforce_auth_rate_limit(redis, action=AuthRateLimitAction.LOGIN, client_ip="127.0.0.1", identifier="user@example.com")
    assert caught.value.headers == {"Retry-After": "300"}
    assert "user@example.com" not in " ".join(redis.keys)
```

Add one fake-Redis failure test proving local development fails open with a warning, and another proving staging/production returns `AuthRateLimitUnavailableException` instead of authenticating without a limit.

In `tests/web/test_auth_abuse.py`, add tests that:

- unknown Web and Admin login attempts invoke the limiter and return the same 401 detail as wrong passwords;
- registration send-code for a new and existing email returns the same message/cooldown shape;
- password-reset send-code for an existing and unknown email returns the same public response;
- a production-mode SMTP failure is logged but its exception text is absent from the response;
- `_hash_code` changes when the domain-separated `SECRET_KEY` changes and is unchanged when only the SMTP password changes.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest -q tests/core/test_auth_rate_limit.py tests/web/test_auth_abuse.py
```

Expected: import/collection fails for the missing limiter and existing send-code endpoints expose account existence.

- [ ] **Step 3: Add validated configuration and 429 exception**

Add configuration defaults:

```python
class AuthAbuseSettings(BaseSettings):
    AUTH_RATE_LIMIT_PREFIX: str = "auth:rate-limit:"
    AUTH_LOGIN_WINDOW_SECONDS: int = 300
    AUTH_LOGIN_IP_LIMIT: int = 30
    AUTH_LOGIN_IDENTIFIER_LIMIT: int = 10
    AUTH_LOGIN_PAIR_LIMIT: int = 5
    AUTH_VERIFICATION_SEND_WINDOW_SECONDS: int = 3600
    AUTH_VERIFICATION_SEND_IP_LIMIT: int = 10
    AUTH_VERIFICATION_SEND_IDENTIFIER_LIMIT: int = 3
    AUTH_VERIFICATION_CHECK_WINDOW_SECONDS: int = 600
    AUTH_VERIFICATION_CHECK_IP_LIMIT: int = 30
    AUTH_VERIFICATION_CHECK_IDENTIFIER_LIMIT: int = 10
```

Include the mixin in `Settings`. Validate every window and limit is positive before the existing production-only early return.

Add:

```python
from fastapi import HTTPException

class TooManyRequestsException(HTTPException):
    def __init__(self, detail: str, retry_after: int):
        super().__init__(
            status_code=429,
            detail=detail,
            headers={"Retry-After": str(max(1, retry_after))},
        )


class AuthRateLimitUnavailableException(HTTPException):
    def __init__(self):
        super().__init__(
            status_code=503,
            detail="Authentication service is temporarily unavailable.",
        )
```

- [ ] **Step 4: Implement atomic, privacy-preserving limiter**

Create `src/app/core/auth_rate_limit.py` with an atomic Lua script:

```python
RATE_LIMIT_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
local ttl = redis.call('TTL', KEYS[1])
return {count, ttl}
"""
```

Define `AuthRateLimitAction(StrEnum)` with `LOGIN`, `VERIFICATION_SEND`, and `VERIFICATION_CHECK`. Normalize identifiers with `strip().lower()`. Build each stored dimension value as:

```python
digest = hmac.new(
    settings.SECRET_KEY.get_secret_value().encode(),
    f"auth-rate-limit:{value}".encode(),
    hashlib.sha256,
).hexdigest()
```

For login, evaluate IP, identifier, and pair rules. For send/check, evaluate IP and identifier. If any returned count exceeds its limit, raise `TooManyRequestsException("Too many requests. Please try again later.", max_retry_after)`.

Catch only `redis.exceptions.RedisError` around Redis evaluation. When `ENVIRONMENT=local`, log a warning and return so ordinary local login remains usable during a Redis outage. In staging/production, raise `AuthRateLimitUnavailableException`; do not authenticate without enforcement.

- [ ] **Step 5: Wire login routes and preserve local bypass**

Add `Redis = Depends(async_get_redis)` to Web/Admin login routes. Before Web authentication:

```python
await enforce_auth_rate_limit(
    redis,
    action=AuthRateLimitAction.LOGIN,
    client_ip=request.client.host if request.client else "unknown",
    identifier=form_data.username,
)
```

In Admin login, perform the same call only when `is_local_dev_auto_login_admin(payload.username_or_email)` is false. Do not trust `X-Forwarded-For` directly.

- [ ] **Step 6: Make verification send/check uniform and domain separated**

Pass `Request` client host from register, register/send-code, password-reset/send-code, and password-reset/confirm into their service calls. Enforce `VERIFICATION_SEND` before account lookup and `VERIFICATION_CHECK` before code lookup.

Replace `_hash_code` with:

```python
def _hash_code(email: str, code: str) -> str:
    message = f"verification-code:{_normalize_email(email)}:{code}".encode()
    secret = settings.SECRET_KEY.get_secret_value().encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()
```

For an existing registration email or unknown reset email, return a normal `VerificationSendResult` without generating/storing/sending a code. Catch SMTP delivery exceptions, delete the unusable code, log a redacted error, and return the same accepted result outside local development. Keep local debug codes only in `ENVIRONMENT=local`.

Replace both existing resend-cooldown `RateLimitException` raises with `TooManyRequestsException` and pass the calculated `retry_after`, so every 429 response includes the header.

Both send endpoints return:

```python
message="If the address is eligible, a verification code will be sent."
```

and never include raw SMTP error text.

- [ ] **Step 7: Verify GREEN**

Run:

```bash
uv run pytest -q tests/core/test_auth_rate_limit.py tests/web/test_auth_abuse.py tests/web/test_auth.py tests/admin/test_auth.py tests/core/test_security_config.py
uv run ruff check --no-fix src/app/core/auth_rate_limit.py src/app/core/config.py src/app/core/exceptions/http_exceptions.py src/app/api/v1/login.py src/app/admin/api/v1/auth.py src/app/api/v1/web_users.py src/app/modules/user/register_verification_service.py tests/core/test_auth_rate_limit.py tests/web/test_auth_abuse.py
uv run mypy src/app/core/auth_rate_limit.py src/app/core/config.py
```

Expected: abuse tests and existing auth flows pass; 429 responses include `Retry-After`; Ruff/mypy exit 0.

- [ ] **Step 8: Commit authentication abuse controls**

```bash
git add src/app/core/auth_rate_limit.py src/app/core/config.py src/app/core/exceptions/http_exceptions.py src/app/api/v1/login.py src/app/admin/api/v1/auth.py src/app/api/v1/web_users.py src/app/modules/user/register_verification_service.py src/.env.example tests/core/test_auth_rate_limit.py tests/web/test_auth_abuse.py
git commit -m "fix: bound authentication abuse"
```

---

### Task 5: Fair concurrent event workers and atomic dead-letter transfer

**Files:**

- Modify: `src/app/core/config.py:150-165,270-300`
- Modify: `src/app/event/mq_client.py`
- Modify: `src/app/event/event_manager.py:1-45`
- Modify: `event_consumer.py:14-30`
- Modify: `tests/event/test_delivery_semantics.py`
- Modify: `src/.env.example:33-42`

**Interfaces:**

- Changes: `AsyncMQClient.start()` owns `EVENT_CONSUMER_CONCURRENCY` worker tasks.
- Produces: `Message.raw_payload`, `Message.decode_error`, and delivery-count lookup.
- Produces: atomic DLQ operation to `<source-stream>:dead-letter`.
- Changes: `AsyncEventManager` routes/stats only; it no longer owns a semaphore or concurrency constructor argument.

- [ ] **Step 1: Replace old delivery tests with failing fairness, concurrency, and DLQ tests**

Extend `tests/event/test_delivery_semantics.py` with fakes recording `xreadgroup`, `xautoclaim`, `xpending_range`, `eval`, and `xack`. Add tests that prove:

```python
@pytest.mark.asyncio
async def test_fetch_alternates_new_and_stale_work() -> None:
    client = AsyncMQClient(QueueType.MISC, group="test-group")
    redis = FairFakeRedis()
    first = await client._get_item(redis, block_time=0)
    second = await client._get_item(redis, block_time=0)
    assert [first.id, second.id] == ["new-1", "stale-1"]


@pytest.mark.asyncio
async def test_poison_message_moves_to_dlq_at_delivery_limit(monkeypatch) -> None:
    monkeypatch.setattr(settings, "EVENT_CONSUMER_MAX_DELIVERIES", 3)
    redis = DeadLetterFakeRedis(delivery_count=3)
    client = AsyncMQClient(QueueType.MISC, group="test-group")
    message = Message(id="9-0", data={"type": "unknown"}, raw_payload='{"type":"unknown"}', decode_error=None, redis_client=redis)
    await client._handle_failure(message, UnhandledEventError("unknown"))
    assert redis.dead_letters[0]["original_message_id"] == "9-0"
    assert redis.acked == [(client._queue, "test-group", "9-0")]
```

Add a worker test with two queued messages and an async barrier; assert two handlers are simultaneously active when concurrency is two. Add a malformed-JSON test proving it is retained below the limit and dead-lettered at the limit.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest -q tests/event/test_delivery_semantics.py tests/event/test_outbox.py tests/event/test_mail_outbox_contract.py
```

Expected: tests fail because `Message` lacks raw/decode fields, fetch always prioritizes stale work, and no delivery/DLQ APIs exist.

- [ ] **Step 3: Add event settings and validation**

Add:

```python
EVENT_CONSUMER_BUFFER_SIZE: int = 12
EVENT_CONSUMER_MAX_DELIVERIES: int = 5
EVENT_CONSUMER_SHUTDOWN_TIMEOUT_SECONDS: float = 10.0
EVENT_DEAD_LETTER_MAXLEN: int = 10_000
EVENT_DEAD_LETTER_RAW_MAX_CHARS: int = 4_000
EVENT_DEAD_LETTER_ERROR_MAX_CHARS: int = 500
```

Validate these and existing `EVENT_CONSUMER_CONCURRENCY`/`EVENT_PENDING_IDLE_MS` are positive. Add all values to `src/.env.example`.

- [ ] **Step 4: Make message decoding retain poison-message identity**

Change `Message` to:

```python
@dataclass(slots=True)
class Message:
    id: str
    data: dict[str, Any] | None
    raw_payload: str
    decode_error: str | None
    redis_client: redis.Redis
```

`_decode_message` catches `json.JSONDecodeError` and non-object JSON, returning a `Message` with `data=None` and a bounded `decode_error`. `_handle_message` raises a dedicated malformed-message error before handler dispatch when `decode_error` or `data is None` is present.

- [ ] **Step 5: Implement fair fetch and bounded workers**

Replace the fixed `buffer_length=1` with `settings.EVENT_CONSUMER_BUFFER_SIZE`. Keep a `_prefer_new` boolean and implement `_get_item` as:

```python
if self._prefer_new:
    message = await self._read_new_item(redis_client, block_time)
    if message is None:
        message = await self._claim_stale_item(redis_client)
else:
    message = await self._claim_stale_item(redis_client)
    if message is None:
        message = await self._read_new_item(redis_client, block_time)
self._prefer_new = not self._prefer_new
return message
```

`start()` creates one fetch task and exactly `EVENT_CONSUMER_CONCURRENCY` worker tasks. Each worker pulls from the bounded queue, calls `_handle_message`, delegates exceptions to `_handle_failure`, and always calls `task_done`. On stop, cancel fetching, wait for `queue.join()` up to `EVENT_CONSUMER_SHUTDOWN_TIMEOUT_SECONDS`, then cancel workers. Never re-enqueue in-flight payloads as new stream entries during cleanup.

- [ ] **Step 6: Implement delivery-count lookup and atomic DLQ transfer**

Use `XPENDING RANGE` for the one message id and read `times_delivered`. Below the maximum, log and leave the message pending. At or above the maximum, call one Lua script that:

```lua
redis.call('XADD', KEYS[2], 'MAXLEN', '~', ARGV[1], '*',
  'original_stream', KEYS[1],
  'original_message_id', ARGV[2],
  'event_id', ARGV[3],
  'event_type', ARGV[4],
  'raw_payload', ARGV[5],
  'delivery_count', ARGV[6],
  'failure_category', ARGV[7],
  'error', ARGV[8],
  'dead_lettered_at', ARGV[9])
return redis.call('XACK', KEYS[1], ARGV[10], ARGV[2])
```

Pass source stream and `<source>:dead-letter` as `KEYS`. Bound raw/error strings from settings and redact exception text using the existing logging/redaction conventions. If the Lua call fails, leave the source pending.

- [ ] **Step 7: Remove ineffective EventManager concurrency**

Delete `_semaphore` and the `concurrency` constructor argument. `receive` calls `_receive` directly. In `event_consumer.py`, construct:

```python
event_manager = AsyncEventManager(stats_interval=settings.EVENT_STATS_INTERVAL)
```

The queue worker count is now the only concurrency control.

- [ ] **Step 8: Verify GREEN**

Run:

```bash
uv run pytest -q tests/event/test_delivery_semantics.py tests/event/test_outbox.py tests/event/test_mail_outbox_contract.py
uv run ruff check --no-fix src/app/event src/app/core/config.py event_consumer.py tests/event
uv run mypy src/app/event/mq_client.py src/app/event/event_manager.py
```

Expected: all event tests pass; worker concurrency is observable; poison/malformed messages are atomically DLQ'd at the configured limit; Ruff/mypy exit 0.

- [ ] **Step 9: Commit event recovery**

```bash
git add src/app/core/config.py src/app/event/mq_client.py src/app/event/event_manager.py event_consumer.py src/.env.example tests/event/test_delivery_semantics.py
git commit -m "fix: isolate poison events in dead letter stream"
```

---

### Task 6: Integration verification and status documentation

**Files:**

- Modify: `docs/server-foundation-hardening-status-zh.md`

**Interfaces:**

- Consumes: all five completed task contracts.
- Produces: current verification evidence and updated remaining-debt record.

- [ ] **Step 1: Run source scans for removed compatibility paths**

Run:

```bash
rg -n "import bcrypt|bcrypt\.|\$2[aby]\$|mimetypes\.guess_type|Failed to send .*\{exc\}|CANDIDATE_REGISTER_VERIFICATION_AUTH_SECRET.*hash" src/app pyproject.toml
```

Expected: no production password/MIME/SMTP-error compatibility matches. The SMTP secret remains only for SMTP login/configuration, not verification-code hashing.

- [ ] **Step 2: Run all safe non-database tests**

Use the repository's existing no-cleanup selection or explicit test list, including all new core/event tests:

```bash
uv run pytest -q -m no_database_cleanup
```

Expected: zero failures.

- [ ] **Step 3: Run disposable database/Redis integration gates**

With a dedicated allowlisted database and disposable Redis only:

```bash
uv run alembic -c src/alembic.ini heads
uv run alembic -c src/alembic.ini upgrade head
uv run pytest -q tests/web/test_job_application_concurrency.py tests/web/test_job_application_errors.py tests/web/test_job_applications.py tests/web/test_auth.py tests/web/test_auth_abuse.py tests/admin/test_auth.py
```

Expected: one Alembic head and zero focused integration failures. Do not substitute the user's existing database if the disposable environment is unavailable.

- [ ] **Step 4: Run repository quality gates**

```bash
uv run ruff check --no-fix src tests event_consumer.py
uv run mypy src/app/core/config.py src/app/core/security.py src/app/core/auth_rate_limit.py src/app/core/auth_sessions.py src/app/event src/app/modules/assets/content_policy.py src/app/modules/assets/responses.py
git diff --check
```

Expected: every command exits 0.

- [ ] **Step 5: Update the status document with evidence**

Add a dated section recording:

- server-owned asset classification and attachment-only non-raster delivery;
- intentional all-Admin asset sharing;
- database-enforced active application uniqueness and atomic counts;
- Argon2id-only password hashes;
- Redis multi-dimensional auth limits and non-enumerating verification responses;
- bounded event workers and DLQ behavior;
- exact tests/gates run, with any disposable-DB gate explicitly marked not run if unavailable.

Keep audit expansion, mail payload bounds, CI lock enforcement, deployment docs, physical asset lifecycle, synchronous object I/O, full-business mypy, large service decomposition, and dynamic list scans in the remaining P2/debt section.

- [ ] **Step 6: Re-run final evidence after documentation changes**

```bash
uv run ruff check --no-fix src tests event_consumer.py
uv run pytest -q -m no_database_cleanup
git diff --check
git status --short
```

Expected: lint/tests/diff check exit 0; status shows only the intentional final task changes.

- [ ] **Step 7: Commit verified status**

```bash
git add docs/server-foundation-hardening-status-zh.md
git commit -m "docs: record direct P0 P1 hardening"
```

## Plan Self-Review Checklist

- Every approved P0/P1 design section maps to one task.
- Admin-wide data access is explicit and tested; no role-based asset restriction is reintroduced.
- No password, MIME, Redis pending-message, response-text, or setting compatibility shim is planned.
- Each production behavior has a preceding failing test and a focused green command.
- The migration has one explicit predecessor and does not silently delete conflicts.
- New foundation modules are included in mypy and Ruff gates.
- Destructive integration testing is constrained to disposable infrastructure.
