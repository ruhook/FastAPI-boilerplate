# Server Foundation Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the five remaining server-foundation gaps so business-layer refactoring starts from one reliable runtime, deployment, security, asset-I/O, mail-recovery, and readiness baseline.

**Architecture:** Keep the existing FastAPI modular monolith and its Web/Admin/MySQL/Redis/outbox topology. Remove obsolete and compatibility paths, add a database-backed mail recovery lease, offload synchronous storage work at async boundaries, and make readiness independent from request transaction commits.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy async ORM, Alembic, MySQL 8.4, Redis 7.4, asyncio, pytest, Ruff, mypy, uv.

## Global Constraints

- Work on the current `codex/candidate-applications-state-contract` branch. Do not create another branch or worktree.
- Do not change recruitment, payment, timesheet, talent, or other business semantics.
- Preserve the intentional rule that every authenticated Admin account may access Admin asset endpoints globally.
- Remove compatibility code instead of maintaining dual paths. Plaintext SMTP credentials and the duplicate Web logout route are not retained.
- Do not add a new infrastructure service.
- Use test-first implementation for every runtime behavior change.
- Keep Alembic history forward-only and maintain a single migration head.
- Canonical runtime processes remain `src.app.main_web:app`, `src.app.main_admin:app`, and `event_consumer.py`.

---

### Task 1: Canonical repository, configuration, and locked CI

**Files:**
- Create: `tests/core/test_repository_contracts.py`
- Modify: `tests/core/test_security_config.py`
- Modify: `src/app/core/config.py`
- Modify: `.github/workflows/tests.yml`
- Modify: `.github/workflows/linting.yml`
- Modify: `.github/workflows/type-checking.yml`
- Modify: `deploy/env/hr-server.production.env.example`
- Delete: `src/app/main.py`
- Delete: `scripts/local_with_uvicorn/`
- Delete: `scripts/gunicorn_managing_uvicorn_workers/`
- Delete: `scripts/production_with_nginx/`

**Interfaces:**
- Produces: `Settings.HEALTH_CHECK_TIMEOUT_SECONDS: float`.
- Produces: `Settings.REDIS_CONNECT_TIMEOUT_SECONDS: float` and `Settings.REDIS_SOCKET_TIMEOUT_SECONDS: float`.
- Produces: `Settings.MAIL_TASK_PROCESSING_LEASE_SECONDS: int`, `MAIL_TASK_RECOVERY_INTERVAL_SECONDS: float`, and `MAIL_TASK_RECOVERY_BATCH_SIZE: int`.
- Preserves: the existing production fail-closed validator and canonical Supervisor deployment.

- [ ] **Step 1: Write failing repository and positive-setting contract tests**

```python
# tests/core/test_repository_contracts.py
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_database_cleanup
ROOT = Path(__file__).resolve().parents[2]


def test_only_canonical_runtime_examples_remain() -> None:
    assert not (ROOT / "src/app/main.py").exists()
    for relative in (
        "scripts/local_with_uvicorn",
        "scripts/gunicorn_managing_uvicorn_workers",
        "scripts/production_with_nginx",
    ):
        assert not (ROOT / relative).exists()

    tracked_text = "\n".join(
        path.read_text(encoding="utf-8")
        for base in (ROOT / "deploy", ROOT / "scripts")
        for path in base.rglob("*")
        if path.is_file()
    )
    for obsolete in ("app.main:app", "poetry", "postgres:13", " arq "):
        assert obsolete not in tracked_text.lower()


def test_ci_uses_frozen_uv_lock() -> None:
    for workflow in (ROOT / ".github/workflows").glob("*.yml"):
        content = workflow.read_text(encoding="utf-8")
        assert "uv sync --frozen --all-extras --all-groups" in content
        assert "uv pip install" not in content
        for command in ("pytest", "ruff", "mypy", "alembic"):
            if f"uv run {command}" in content:
                assert f"uv run --frozen {command}" in content


def test_production_env_example_lists_required_foundation_settings() -> None:
    content = (ROOT / "deploy/env/hr-server.production.env.example").read_text(encoding="utf-8")
    required = {
        "MAIL_CREDENTIAL_ENCRYPTION_KEY",
        "HEALTH_CHECK_TIMEOUT_SECONDS",
        "REDIS_CONNECT_TIMEOUT_SECONDS",
        "REDIS_SOCKET_TIMEOUT_SECONDS",
        "MAIL_TASK_PROCESSING_LEASE_SECONDS",
        "MAIL_TASK_RECOVERY_INTERVAL_SECONDS",
        "MAIL_TASK_RECOVERY_BATCH_SIZE",
        "ENABLE_LOCAL_AUTH_BYPASS",
        "ENABLE_LOCAL_ADMIN_BOOTSTRAP",
    }
    assert all(f"{name}=" in content for name in required)
    assert "ACCESS_TOKEN_EXPIRE_MINUTES=15" in content
    assert "ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES=15" in content
```

Append to `tests/core/test_security_config.py`:

```python
@pytest.mark.parametrize(
    "setting_name",
    [
        "HEALTH_CHECK_TIMEOUT_SECONDS",
        "REDIS_CONNECT_TIMEOUT_SECONDS",
        "REDIS_SOCKET_TIMEOUT_SECONDS",
        "MAIL_TASK_PROCESSING_LEASE_SECONDS",
        "MAIL_TASK_RECOVERY_INTERVAL_SECONDS",
        "MAIL_TASK_RECOVERY_BATCH_SIZE",
    ],
)
def test_foundation_runtime_limits_must_be_positive(setting_name: str) -> None:
    with pytest.raises(ValidationError, match=setting_name):
        Settings(_env_file=None, ENVIRONMENT="local", **{setting_name: 0})
```

- [ ] **Step 2: Run the new tests and verify they fail for the obsolete paths, unfrozen workflows, missing settings, and missing validation**

Run:

```bash
.venv/bin/pytest -q tests/core/test_repository_contracts.py tests/core/test_security_config.py
```

Expected: assertion failures for the tracked obsolete paths/workflow commands and validation failures because the six settings do not yet exist in the positive-setting catalog.

- [ ] **Step 3: Add the runtime settings and positive validation**

Add these settings classes to `src/app/core/config.py` and include them in `Settings` inheritance:

```python
class RedisCacheSettings(BaseSettings):
    REDIS_CACHE_HOST: str = "localhost"
    REDIS_CACHE_PORT: int = 6379
    REDIS_CONNECT_TIMEOUT_SECONDS: float = 2.0
    REDIS_SOCKET_TIMEOUT_SECONDS: float = 2.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def REDIS_CACHE_URL(self) -> str:
        return f"redis://{self.REDIS_CACHE_HOST}:{self.REDIS_CACHE_PORT}"


class MailTaskRecoverySettings(BaseSettings):
    MAIL_TASK_PROCESSING_LEASE_SECONDS: int = 120
    MAIL_TASK_RECOVERY_INTERVAL_SECONDS: float = 30.0
    MAIL_TASK_RECOVERY_BATCH_SIZE: int = 50


class HealthSettings(BaseSettings):
    HEALTH_CHECK_TIMEOUT_SECONDS: float = 2.0
```

Add all six names to `positive_setting_names` in `validate_runtime_security`.

- [ ] **Step 4: Replace CI installation and commands with frozen uv usage**

Each workflow installation step becomes:

```yaml
    - name: Sync locked dependencies
      run: uv sync --frozen --all-extras --all-groups
```

Use the corresponding frozen command in each job:

```yaml
run: uv run --frozen pytest
run: uv run --frozen ruff check --no-fix src tests
run: >-
  uv run --frozen mypy
  src/app/core
  src/app/event
  src/app/modules/auth_refresh_session
  src/app/modules/event_outbox
  src/app/modules/admin/role
  src/app/modules/assets
  --config-file pyproject.toml
run: uv run --frozen alembic upgrade head
```

- [ ] **Step 5: Remove obsolete entrypoints and synchronize the production environment example**

Delete only the empty `src/app/main.py` and the three obsolete Docker-example directories. Preserve `scripts/local_admin_bootstrap.test.py` and every `deploy/` Supervisor/Nginx example.

Update `deploy/env/hr-server.production.env.example` to include the following exact foundation values alongside its real deployment-specific placeholders:

```dotenv
SECRET_KEY="replace-with-a-real-secret"
ACCESS_TOKEN_EXPIRE_MINUTES=15
ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES=15
ENABLE_LOCAL_AUTH_BYPASS=false
ENABLE_LOCAL_ADMIN_BOOTSTRAP=false
REDIS_CONNECT_TIMEOUT_SECONDS=2
REDIS_SOCKET_TIMEOUT_SECONDS=2
HEALTH_CHECK_TIMEOUT_SECONDS=2
EVENT_CONSUMER_CONCURRENCY=3
EVENT_CONSUMER_BUFFER_SIZE=12
EVENT_CONSUMER_MAX_DELIVERIES=5
EVENT_PENDING_IDLE_MS=60000
EVENT_OUTBOX_BATCH_SIZE=50
EVENT_OUTBOX_LEASE_SECONDS=60
EVENT_OUTBOX_POLL_SECONDS=1
MAIL_DELIVERY_MODE="smtp"
MAIL_CREDENTIAL_ENCRYPTION_KEY="replace-with-a-fernet-key"
MAIL_TASK_PROCESSING_LEASE_SECONDS=120
MAIL_TASK_RECOVERY_INTERVAL_SECONDS=30
MAIL_TASK_RECOVERY_BATCH_SIZE=50
ASSET_STORAGE_PROVIDER="local"
```

Keep candidate verification and Aliyun OSS values explicit; placeholders for enabled production services must fail the existing production validator until replaced.

- [ ] **Step 6: Run Task 1 tests and Ruff**

Run:

```bash
.venv/bin/pytest -q tests/core/test_repository_contracts.py tests/core/test_security_config.py
.venv/bin/ruff check --no-fix src/app/core/config.py tests/core/test_repository_contracts.py tests/core/test_security_config.py
```

Expected: all selected tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 7: Commit Task 1**

```bash
git add .github deploy/env src/app/core/config.py tests/core/test_repository_contracts.py tests/core/test_security_config.py scripts src/app/main.py
git commit -m "chore: canonicalize server runtime and locked CI"
```

---

### Task 2: Remove duplicate logout and plaintext mail compatibility

**Files:**
- Modify: `tests/core/test_mail_account_credentials.py`
- Modify: `tests/web/test_auth.py`
- Modify: `tests/admin/mail/test_accounts.py`
- Modify: `tests/admin/test_jobs.py`
- Modify: `tests/web/test_job_progress.py`
- Modify: `src/app/api/v1/web.py`
- Delete: `src/app/api/v1/logout.py`
- Modify: `src/app/modules/admin/mail_account/model.py`
- Modify: `src/app/modules/admin/mail_account/schema.py`
- Modify: `src/app/modules/admin/mail_account/service.py`
- Modify: `src/scripts/seed_job_progress_demo_flow.py`
- Delete: `src/scripts/encrypt_mail_account_credentials.py`
- Delete: `tests/scripts/test_encrypt_mail_account_credentials.py`
- Create: `src/migrations/versions/20260710_000046_drop_plaintext_mail_secret.py`
- Modify: `docs/deployment-zh.md`

**Interfaces:**
- Preserves: `POST /api/v1/logout`, `MailAccountCreate.auth_secret`, and `MailAccountUpdate.auth_secret`.
- Changes: persistent `MailAccount` contains only `auth_secret_encrypted`.
- Changes: `resolve_mail_account_auth_secret(account: MailAccount) -> str` decrypts only `auth_secret_encrypted`.

- [ ] **Step 1: Write failing direct-contract tests**

Add to `tests/web/test_auth.py`:

```python
def test_web_application_registers_one_logout_route() -> None:
    from src.app.main_web import app

    routes = [
        route
        for route in app.routes
        if route.path == "/api/v1/logout" and route.methods == {"POST"}
    ]
    assert len(routes) == 1
```

Refactor `tests/core/test_mail_account_credentials.py` so `build_account` accepts only `auth_secret_encrypted`, constructs `MailAccount` without `auth_secret`, and add:

```python
def test_mail_account_model_has_no_plaintext_secret_column() -> None:
    assert "auth_secret" not in MailAccount.__table__.columns


def test_resolver_rejects_account_without_encrypted_credentials() -> None:
    account = build_account(auth_secret_encrypted=None)

    with pytest.raises(ValueError, match="not configured"):
        resolve_mail_account_auth_secret(account)
```

Update create/update assertions to require encrypted ciphertext and never inspect a plaintext model attribute.

- [ ] **Step 2: Run the direct-contract tests and verify failures**

Run:

```bash
.venv/bin/pytest -q tests/core/test_mail_account_credentials.py tests/web/test_auth.py::test_web_application_registers_one_logout_route
```

Expected: the route-count assertion reports two routes and the model-column assertion reports the existing `auth_secret` column.

- [ ] **Step 3: Keep one logout route**

Remove the `logout_router` import and `router.include_router(logout_router)` from `src/app/api/v1/web.py`, then delete `src/app/api/v1/logout.py`. Keep the logout function in `src/app/api/v1/login.py` unchanged so it continues to call `revoke_refresh_token` before deleting the cookie.

- [ ] **Step 4: Remove plaintext mail storage from runtime code and fixtures**

The model fields become:

```python
auth_secret_encrypted: Mapped[str | None] = mapped_column(String(1024), nullable=True)
```

Replace the serializer's credential-presence expression with:

```python
has_auth_secret=bool(account.auth_secret_encrypted),
```

Replace the resolver with:

```python
def resolve_mail_account_auth_secret(account: MailAccount) -> str:
    encrypted = (account.auth_secret_encrypted or "").strip()
    if not encrypted:
        raise ValueError("Mail account authentication credential is not configured.")
    return decrypt_credential(encrypted)
```

Create/update assign only `account.auth_secret_encrypted = encrypt_credential(payload.auth_secret)`. Remove `auth_secret` from the internal persistence schemas and from all `MailAccount(...)` fixtures/seeds. Keep the public create/update request field named `auth_secret` because it is a write-only transport field, not a compatibility storage path.

- [ ] **Step 5: Add the forward migration and delete the one-off compatibility command**

Create `20260710_000046_drop_plaintext_mail_secret.py`:

```python
"""drop plaintext mail account credential

Revision ID: 20260710_000046
Revises: 20260710_000045
Create Date: 2026-07-10 23:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260710_000046"
down_revision: str | None = "20260710_000045"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("mail_account", "auth_secret")


def downgrade() -> None:
    op.add_column("mail_account", sa.Column("auth_secret", sa.String(length=255), nullable=True))
```

Delete `src/scripts/encrypt_mail_account_credentials.py` and its test. Remove the deployment command and compatibility-window explanation from `docs/deployment-zh.md`; retain Fernet key generation and key-stability instructions.

- [ ] **Step 6: Run Task 2 tests, route introspection, and migration-head check**

Run:

```bash
.venv/bin/pytest -q tests/core/test_mail_account_credentials.py tests/web/test_auth.py tests/admin/mail/test_accounts.py
.venv/bin/python -c 'from collections import Counter; from src.app.main_web import app; c=Counter((tuple(sorted(r.methods or [])), r.path) for r in app.routes); assert c[("POST",), "/api/v1/logout"] == 1'
cd src && ../.venv/bin/alembic heads
```

Expected: selected tests pass, route assertion exits 0, and Alembic prints only `20260710_000046 (head)`.

- [ ] **Step 7: Commit Task 2**

```bash
git add src/app/api/v1 src/app/modules/admin/mail_account src/scripts src/migrations/versions tests docs/deployment-zh.md
git commit -m "refactor: remove server compatibility paths"
```

---

### Task 3: Recover mail tasks left active by process exit

**Files:**
- Modify: `tests/event/test_mail_outbox_contract.py`
- Create: `tests/event/test_mail_task_recovery.py`
- Modify: `src/app/modules/admin/mail_task/model.py`
- Modify: `src/app/modules/admin/mail_task/service.py`
- Create: `src/app/modules/admin/mail_task/recovery.py`
- Modify: `event_consumer.py`
- Create: `src/migrations/versions/20260710_000047_mail_task_processing_lease.py`

**Interfaces:**
- Produces: `resolve_stale_mail_task_recovery(current_status: str, delivery_mode: str) -> tuple[str, bool]`.
- Produces: `recover_stale_mail_tasks(db: AsyncSession, *, now: datetime, delivery_mode: str, limit: int) -> int`.
- Produces: `MailTaskRecoveryWorker.run() -> None` and `MailTaskRecoveryWorker.stop() -> None`.
- Adds: `MailTask.processing_started_at` and `MailTask.processing_lease_expires_at`.

- [ ] **Step 1: Write failing model and recovery-decision tests**

Append to `tests/event/test_mail_outbox_contract.py`:

```python
@pytest.mark.parametrize(
    ("status", "delivery_mode", "expected"),
    [
        (MailTaskStatus.RENDERING.value, "smtp", (MailTaskStatus.RETRYING.value, True)),
        (MailTaskStatus.SENDING.value, "smtp", (MailTaskStatus.DELIVERY_UNKNOWN.value, False)),
        (MailTaskStatus.SENDING.value, "preview", (MailTaskStatus.RETRYING.value, True)),
    ],
)
def test_stale_mail_task_recovery_decision(
    status: str,
    delivery_mode: str,
    expected: tuple[str, bool],
) -> None:
    resolver = getattr(mail_task_service, "resolve_stale_mail_task_recovery", None)
    assert resolver is not None
    assert resolver(current_status=status, delivery_mode=delivery_mode) == expected


def test_mail_task_model_has_processing_lease_columns() -> None:
    from src.app.modules.admin.mail_task.model import MailTask

    assert "processing_started_at" in MailTask.__table__.columns
    assert "processing_lease_expires_at" in MailTask.__table__.columns
```

- [ ] **Step 2: Run the decision tests and verify assertion failures**

Run:

```bash
.venv/bin/pytest -q tests/event/test_mail_outbox_contract.py
```

Expected: assertions fail because the resolver and model columns do not exist.

- [ ] **Step 3: Add processing lease fields and state helpers**

Add nullable timezone-aware fields to `MailTask`:

```python
processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
processing_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
```

Add to `mail_task/service.py`:

```python
def resolve_stale_mail_task_recovery(*, current_status: str, delivery_mode: str) -> tuple[str, bool]:
    if current_status == MailTaskStatus.RENDERING.value:
        return MailTaskStatus.RETRYING.value, True
    if current_status == MailTaskStatus.SENDING.value and delivery_mode == "preview":
        return MailTaskStatus.RETRYING.value, True
    if current_status == MailTaskStatus.SENDING.value and delivery_mode == "smtp":
        return MailTaskStatus.DELIVERY_UNKNOWN.value, False
    raise ValueError(f"Mail task status is not recoverable: {current_status}")


def set_mail_task_processing_lease(task: MailTask, *, now: datetime | None = None) -> None:
    timestamp = now or datetime.now(UTC)
    task.processing_started_at = timestamp
    task.processing_lease_expires_at = timestamp + timedelta(seconds=settings.MAIL_TASK_PROCESSING_LEASE_SECONDS)


def clear_mail_task_processing_lease(task: MailTask) -> None:
    task.processing_started_at = None
    task.processing_lease_expires_at = None
```

Import `timedelta`. Call `set_mail_task_processing_lease` before committing `rendering` and again before committing `sending`. Call `clear_mail_task_processing_lease` for `sent`, `failed`, and `delivery_unknown`, including early account-validation failures and the exception handler.

- [ ] **Step 4: Add and run failing database recovery tests**

Create `tests/event/test_mail_task_recovery.py` using the real allowlisted database:

```python
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.modules.admin.mail_account.model import MailAccount
from src.app.modules.admin.mail_task.const import MailTaskStatus
from src.app.modules.admin.mail_task.model import MailTask
from src.app.modules.admin.mail_task.recovery import recover_stale_mail_tasks
from src.app.modules.event_outbox.model import EventOutbox


async def seed_task(
    db: AsyncSession,
    *,
    status: str,
    lease_expires_at: datetime | None,
) -> MailTask:
    account = MailAccount(
        admin_user_id=None,
        email="recovery@example.com",
        provider="qq",
        smtp_username="recovery@example.com",
        smtp_host="smtp.example.com",
        smtp_port=465,
        security_mode="ssl",
        auth_secret_encrypted="v1:not-used-by-recovery",
        status="enabled",
        note=None,
        data={},
    )
    db.add(account)
    await db.flush()
    task = MailTask(
        account_id=account.id,
        template_id=None,
        signature_id=None,
        subject="Recovery test",
        body_html="<p>Recovery test</p>",
        to_recipients=[{"email": "candidate@example.com"}],
        cc_recipients=[],
        bcc_recipients=[],
        attachment_asset_ids=[],
        status=status,
        processing_started_at=(lease_expires_at - timedelta(seconds=60)) if lease_expires_at else None,
        processing_lease_expires_at=lease_expires_at,
        data={},
    )
    db.add(task)
    await db.flush()
    return task


@pytest.mark.asyncio
async def test_recovery_requeues_stale_rendering_task(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    task = await seed_task(
        db_session,
        status=MailTaskStatus.RENDERING.value,
        lease_expires_at=now - timedelta(seconds=1),
    )
    count = await recover_stale_mail_tasks(
        db_session,
        now=now,
        delivery_mode="smtp",
        limit=10,
    )
    await db_session.refresh(task)
    assert count == 1
    assert task.status == MailTaskStatus.RETRYING.value
    assert task.processing_lease_expires_at is None
    outbox_rows = list((await db_session.execute(select(EventOutbox))).scalars().all())
    assert [row.payload["mail_task_id"] for row in outbox_rows] == [task.id]


@pytest.mark.asyncio
async def test_recovery_marks_stale_smtp_send_unknown_without_retry(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    task = await seed_task(
        db_session,
        status=MailTaskStatus.SENDING.value,
        lease_expires_at=now - timedelta(seconds=1),
    )
    count = await recover_stale_mail_tasks(
        db_session,
        now=now,
        delivery_mode="smtp",
        limit=10,
    )
    await db_session.refresh(task)
    assert count == 1
    assert task.status == MailTaskStatus.DELIVERY_UNKNOWN.value
    assert await db_session.scalar(select(func.count(EventOutbox.id))) == 0


@pytest.mark.asyncio
async def test_recovery_requeues_stale_preview_send(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    task = await seed_task(
        db_session,
        status=MailTaskStatus.SENDING.value,
        lease_expires_at=now - timedelta(seconds=1),
    )
    count = await recover_stale_mail_tasks(
        db_session,
        now=now,
        delivery_mode="preview",
        limit=10,
    )
    await db_session.refresh(task)
    assert count == 1
    assert task.status == MailTaskStatus.RETRYING.value
    assert await db_session.scalar(select(func.count(EventOutbox.id))) == 1


@pytest.mark.asyncio
async def test_recovery_ignores_fresh_task(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    await seed_task(
        db_session,
        status=MailTaskStatus.RENDERING.value,
        lease_expires_at=now + timedelta(seconds=60),
    )
    count = await recover_stale_mail_tasks(
        db_session,
        now=now,
        delivery_mode="smtp",
        limit=10,
    )
    assert count == 0


@pytest.mark.asyncio
async def test_recovery_ignores_terminal_task(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    await seed_task(
        db_session,
        status=MailTaskStatus.SENT.value,
        lease_expires_at=now - timedelta(seconds=1),
    )
    count = await recover_stale_mail_tasks(
        db_session,
        now=now,
        delivery_mode="smtp",
        limit=10,
    )
    assert count == 0
```

Run after upgrading the disposable database through migration `000047`; expected failures report the missing `recover_stale_mail_tasks` implementation.

- [ ] **Step 5: Implement recovery query and worker**

Create `mail_task/recovery.py` with these boundaries:

```python
async def recover_stale_mail_tasks(
    db: AsyncSession,
    *,
    now: datetime,
    delivery_mode: str,
    limit: int,
) -> int:
    result = await db.execute(
        select(MailTask)
        .where(
            MailTask.status.in_([MailTaskStatus.RENDERING.value, MailTaskStatus.SENDING.value]),
            MailTask.processing_lease_expires_at.is_not(None),
            MailTask.processing_lease_expires_at <= now,
        )
        .order_by(MailTask.id.asc())
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    tasks = list(result.scalars().all())
    for task in tasks:
        previous_status = task.status
        next_status, should_enqueue = resolve_stale_mail_task_recovery(
            current_status=previous_status,
            delivery_mode=delivery_mode,
        )
        task.status = next_status
        task.error_message = "Mail worker exited before delivery state was finalized."
        task.updated_at = now
        clear_mail_task_processing_lease(task)
        if should_enqueue:
            await enqueue_event(db, EventType.MAIL_TASK_CREATED, {"mail_task_id": task.id})
        logger.warning(
            "Recovered stale mail task",
            extra={
                "mail_task_id": task.id,
                "previous_status": previous_status,
                "recovered_status": next_status,
            },
        )
    await db.flush()
    return len(tasks)
```

`MailTaskRecoveryWorker.run` opens `local_session`, calls the function with current settings, commits, catches/logs non-cancellation exceptions, and waits on a stop event for `MAIL_TASK_RECOVERY_INTERVAL_SECONDS`. `stop()` sets that event.

Start the worker beside the existing outbox dispatcher in `event_consumer.py`; stop and await both tasks in `finally`.

- [ ] **Step 6: Add the lease migration**

Create migration `000047` revising `000046`, adding the two nullable `DateTime(timezone=True)` columns and a non-unique composite index named `ix_mail_task_recovery` on `status, processing_lease_expires_at`. Downgrade drops that index and both columns.

- [ ] **Step 7: Run mail recovery tests and Ruff**

Run:

```bash
cd src && ../.venv/bin/alembic upgrade head
cd .. && env ALLOW_TEST_DATABASE_CLEANUP=true .venv/bin/pytest -q tests/event/test_mail_outbox_contract.py tests/event/test_mail_task_recovery.py tests/admin/mail/test_domain.py
.venv/bin/ruff check --no-fix event_consumer.py src/app/modules/admin/mail_task tests/event
```

Expected: all selected tests pass, Ruff passes, and Alembic has one `000047` head.

- [ ] **Step 8: Commit Task 3**

```bash
git add event_consumer.py src/app/modules/admin/mail_task src/migrations/versions/20260710_000047_mail_task_processing_lease.py tests/event
git commit -m "fix: recover abandoned mail task processing"
```

---

### Task 4: Offload synchronous asset work from async execution

**Files:**
- Modify: `tests/core/test_asset_safety.py`
- Modify: `src/app/modules/assets/service.py`
- Modify: `src/app/modules/admin/mail_task/service.py`
- Modify: `src/app/admin/api/v1/settings/assets.py`

**Interfaces:**
- Produces: `async_store_asset_content`, `async_delete_asset_content`, `async_read_asset_content`, `async_classify_asset_content`, and `async_build_asset_pdf_export`.
- Preserves: existing upload, download, authorization, MIME policy, byte limits, Admin global access, and HTTP response contracts.

- [ ] **Step 1: Write failing async-boundary tests**

Append focused tests to `tests/core/test_asset_safety.py`:

```python
@pytest.mark.asyncio
async def test_asset_storage_helpers_offload_sync_functions(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, tuple[object, ...]]] = []

    async def fake_to_thread(function, *args, **kwargs):
        calls.append((function, args))
        return function(*args, **kwargs)

    monkeypatch.setattr(asset_service.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(asset_service, "store_asset_content", lambda **kwargs: None)
    monkeypatch.setattr(asset_service, "delete_asset_content", lambda storage_key: None)
    monkeypatch.setattr(asset_service, "read_asset_content", lambda asset: b"content")

    await asset_service.async_store_asset_content(storage_key="key", content=b"x", mime_type="text/plain")
    await asset_service.async_delete_asset_content("key")
    assert await asset_service.async_read_asset_content(build_asset()) == b"content"
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_batch_zip_offloads_pdf_conversion_and_archive_write(monkeypatch: pytest.MonkeyPatch) -> None:
    offloaded_names: list[str] = []
    real_to_thread = asyncio.to_thread

    async def recording_to_thread(function, *args, **kwargs):
        offloaded_names.append(getattr(function, "__name__", function.__class__.__name__))
        return await real_to_thread(function, *args, **kwargs)

    monkeypatch.setattr(admin_assets_api.asyncio, "to_thread", recording_to_thread)
    asset = build_asset()

    async def fake_get_asset(_asset_id: int, _db) -> dict:
        return serialize_asset(asset)

    async def fake_get_content(_asset_id: int, _db) -> tuple[Asset, bytes]:
        return asset, b"%PDF-1.7\nasset"

    async def allow_access(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(admin_assets_api, "get_asset", fake_get_asset)
    monkeypatch.setattr(admin_assets_api, "get_asset_content", fake_get_content)
    monkeypatch.setattr(admin_assets_api, "ensure_current_admin_can_access_asset", allow_access)

    response = await admin_assets_api.download_assets_as_zip(
        payload=admin_assets_api.AssetBatchDownloadZipPayload(asset_ids=[asset.id], format="pdf"),
        db=SimpleNamespace(),  # type: ignore[arg-type]
        current_admin={"id": 1, "is_superuser": True},
    )
    assert isinstance(response, StreamingResponse)
    assert "build_asset_pdf_export" in offloaded_names
    assert "writestr" in offloaded_names
    assert "close" in offloaded_names
```

- [ ] **Step 2: Run the asset tests and verify missing-helper failures**

Run:

```bash
.venv/bin/pytest -q tests/core/test_asset_safety.py
```

Expected: failures report missing async storage helpers and synchronous archive operations.

- [ ] **Step 3: Add async wrappers and use them in asset services**

Add to `src/app/modules/assets/service.py`:

```python
async def async_store_asset_content(*, storage_key: str, content: bytes, mime_type: str) -> None:
    await asyncio.to_thread(store_asset_content, storage_key=storage_key, content=content, mime_type=mime_type)


async def async_delete_asset_content(storage_key: str) -> None:
    await asyncio.to_thread(delete_asset_content, storage_key)


async def async_read_asset_content(asset: Asset) -> bytes:
    return await asyncio.to_thread(read_asset_content, asset)


async def async_classify_asset_content(filename: str, content: bytes):
    return await asyncio.to_thread(classify_asset_content, filename, content)


async def async_build_asset_pdf_export(asset: Asset, content: bytes) -> bytes:
    return await asyncio.to_thread(build_asset_pdf_export, asset, content)
```

Import `asyncio`. Use these wrappers in `upload_asset`, `create_asset_from_bytes`, compensation cleanup, and `get_asset_content`. Keep the synchronous primitives private to thread execution and synchronous tests.

- [ ] **Step 4: Offload mail attachment reading and Admin ZIP CPU/I/O**

In `process_mail_task`:

```python
attachment_payloads = await asyncio.to_thread(_resolve_attachment_payloads, task, assets_by_id)
```

In the Admin ZIP endpoint, create `ZipFile` normally but offload all potentially blocking work:

```python
archive = ZipFile(output, "w", ZIP_DEFLATED)
try:
    # existing asset loop and byte-limit checks remain
    if payload.format == "pdf":
        content = await async_build_asset_pdf_export(asset, content)
    await asyncio.to_thread(archive.writestr, _safe_zip_member_name(filename, used_names), content)
    # after the loop
    await asyncio.to_thread(archive.close)
    await asyncio.to_thread(output.seek, 0)
except Exception:
    await asyncio.to_thread(archive.close)
    output.close()
    raise
```

Guard archive close with a boolean so it executes exactly once on both success and failure.

- [ ] **Step 5: Run asset and mail tests plus Ruff**

Run:

```bash
.venv/bin/pytest -q tests/core/test_asset_safety.py tests/core/test_asset_content_policy.py tests/event/test_mail_outbox_contract.py
.venv/bin/ruff check --no-fix src/app/modules/assets/service.py src/app/modules/admin/mail_task/service.py src/app/admin/api/v1/settings/assets.py tests/core/test_asset_safety.py
```

Expected: selected tests and Ruff pass; Admin global-access assertion remains green.

- [ ] **Step 6: Commit Task 4**

```bash
git add src/app/modules/assets/service.py src/app/modules/admin/mail_task/service.py src/app/admin/api/v1/settings/assets.py tests/core/test_asset_safety.py
git commit -m "perf: offload blocking asset operations"
```

---

### Task 5: Dedicated concurrent readiness checks

**Files:**
- Create: `tests/core/test_health.py`
- Modify: `src/app/core/health.py`
- Modify: `src/app/api/v1/health.py`
- Modify: `src/app/core/setup.py`

**Interfaces:**
- Changes: `check_database_health() -> bool` owns a dedicated engine connection.
- Preserves: `check_redis_health(redis: Redis) -> bool` public result contract.
- Changes: `/ready` depends only on Redis client and `Request`, not `async_get_db`.

- [ ] **Step 1: Write failing health tests**

Create `tests/core/test_health.py`:

```python
import asyncio
from types import SimpleNamespace

import pytest

from src.app.api.v1 import health as health_api
from src.app.core import health as health_service
from src.app.core import setup
from src.app.core.config import settings

pytestmark = pytest.mark.no_database_cleanup


@pytest.mark.asyncio
async def test_database_health_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    async def blocked_probe() -> None:
        await asyncio.sleep(1)

    monkeypatch.setattr(health_service, "_probe_database", blocked_probe)
    monkeypatch.setattr(settings, "HEALTH_CHECK_TIMEOUT_SECONDS", 0.01)
    assert await health_service.check_database_health() is False


@pytest.mark.asyncio
async def test_ready_runs_database_and_redis_checks_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    both_started = asyncio.Event()
    started = 0

    async def check(*args, **kwargs) -> bool:
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.2)
        return True

    monkeypatch.setattr(health_api, "check_database_health", check)
    monkeypatch.setattr(health_api, "check_redis_health", check)
    initialized = asyncio.Event()
    initialized.set()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(initialization_complete=initialized)))

    response = await health_api.ready(request=request, redis=SimpleNamespace())
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_ready_requires_initialized_app(monkeypatch: pytest.MonkeyPatch) -> None:
    async def healthy(*args, **kwargs) -> bool:
        return True

    monkeypatch.setattr(health_api, "check_database_health", healthy)
    monkeypatch.setattr(health_api, "check_redis_health", healthy)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(initialization_complete=asyncio.Event())))

    response = await health_api.ready(request=request, redis=SimpleNamespace())
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_redis_pool_uses_bounded_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def capture(url: str, **kwargs: object):
        captured.update(url=url, **kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(setup.redis.ConnectionPool, "from_url", capture)
    monkeypatch.setattr(setup.redis.Redis, "from_pool", lambda pool: SimpleNamespace())
    await setup.create_redis_cache_pool()
    assert captured["socket_connect_timeout"] == settings.REDIS_CONNECT_TIMEOUT_SECONDS
    assert captured["socket_timeout"] == settings.REDIS_SOCKET_TIMEOUT_SECONDS
```

- [ ] **Step 2: Run health tests and verify signature/timeout/concurrency failures**

Run:

```bash
.venv/bin/pytest -q tests/core/test_health.py
```

Expected: failures report missing `_probe_database`, the old `ready` signature requiring a database Session, and missing Redis timeout kwargs.

- [ ] **Step 3: Implement bounded dedicated probes**

Replace `src/app/core/health.py` with dedicated probes:

```python
import asyncio
import logging

from redis.asyncio import Redis
from sqlalchemy import text

from .config import settings
from .db.database import async_engine

LOGGER = logging.getLogger(__name__)


async def _probe_database() -> None:
    async with async_engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def _run_probe(probe) -> bool:
    try:
        async with asyncio.timeout(settings.HEALTH_CHECK_TIMEOUT_SECONDS):
            await probe()
        return True
    except Exception:
        LOGGER.exception("Dependency health check failed")
        return False


async def check_database_health() -> bool:
    return await _run_probe(_probe_database)


async def check_redis_health(redis: Redis) -> bool:
    async def probe() -> None:
        await redis.ping()

    return await _run_probe(probe)
```

- [ ] **Step 4: Run readiness probes concurrently and honor initialization state**

Change `/ready` to accept `Request` and Redis only:

```python
@router.get("/ready", response_model=ReadyCheck)
async def ready(
    request: Request,
    redis: Annotated[Redis, Depends(async_get_redis)],
) -> JSONResponse:
    database_status, redis_status = await asyncio.gather(
        check_database_health(),
        check_redis_health(redis),
    )
    initialization = getattr(request.app.state, "initialization_complete", None)
    app_status = bool(initialization is not None and initialization.is_set())
    overall_status = STATUS_HEALTHY if app_status and database_status and redis_status else STATUS_UNHEALTHY
    http_status = status.HTTP_200_OK if overall_status == STATUS_HEALTHY else status.HTTP_503_SERVICE_UNAVAILABLE
    response = {
        "status": overall_status,
        "environment": settings.ENVIRONMENT.value,
        "version": settings.APP_VERSION,
        "app": STATUS_HEALTHY if app_status else STATUS_UNHEALTHY,
        "database": STATUS_HEALTHY if database_status else STATUS_UNHEALTHY,
        "redis": STATUS_HEALTHY if redis_status else STATUS_UNHEALTHY,
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    return JSONResponse(status_code=http_status, content=response)
```

Keep `/health` process-only. Do not expose exception strings.

- [ ] **Step 5: Configure Redis pool timeouts**

Update `create_redis_cache_pool`:

```python
cache.pool = redis.ConnectionPool.from_url(
    settings.REDIS_CACHE_URL,
    socket_connect_timeout=settings.REDIS_CONNECT_TIMEOUT_SECONDS,
    socket_timeout=settings.REDIS_SOCKET_TIMEOUT_SECONDS,
)
cache.client = redis.Redis.from_pool(cache.pool)  # type: ignore
```

- [ ] **Step 6: Run health tests and Ruff**

Run:

```bash
.venv/bin/pytest -q tests/core/test_health.py tests/core/test_security_config.py
.venv/bin/ruff check --no-fix src/app/core/health.py src/app/api/v1/health.py src/app/core/setup.py tests/core/test_health.py
```

Expected: all selected tests and Ruff pass.

- [ ] **Step 7: Commit Task 5**

```bash
git add src/app/core/health.py src/app/api/v1/health.py src/app/core/setup.py tests/core/test_health.py
git commit -m "fix: bound dependency readiness checks"
```

---

### Task 6: Final documentation and quality gate

**Files:**
- Modify: `docs/development-minimal-zh.md`
- Modify: `docs/deployment-zh.md`
- Modify: `deploy/supervisor/hr-server.production.conf.example` only if the recovery worker changes its process command or environment requirements.

**Interfaces:**
- Documents: one canonical deployment, encrypted-only SMTP storage, mail recovery states, and readiness behavior.
- Produces no new runtime API.

- [ ] **Step 1: Update operational documentation**

Document these exact behaviors:

- Supervisor starts Web, Admin, and one event-consumer process.
- Event consumer owns both outbox dispatch and stale-mail recovery.
- `delivery_unknown` requires operator review and is never automatically resent.
- `MAIL_CREDENTIAL_ENCRYPTION_KEY` is required and plaintext database credentials are unsupported.
- `/health` is liveness; `/ready` requires initialized app, MySQL, and Redis within the configured timeout.
- CI and local reproducible installs use `uv sync --frozen --all-extras --all-groups`.

- [ ] **Step 2: Run migration, repository, targeted, and full verification**

Run:

```bash
uv lock --check
cd src && ../.venv/bin/alembic heads
cd .. && .venv/bin/ruff check --no-fix src tests event_consumer.py
.venv/bin/mypy src/app/core/config.py src/app/core/health.py src/app/core/setup.py src/app/core/db/database.py src/app/core/utils/cache.py src/app/event src/app/modules/event_outbox src/app/modules/auth_refresh_session src/app/modules/admin/mail_task src/app/modules/admin/mail_account src/app/modules/assets
env ALLOW_TEST_DATABASE_CLEANUP=true .venv/bin/pytest -q
```

Expected:

- `uv lock --check` exits 0.
- Alembic prints one `20260710_000047 (head)`.
- Ruff and mypy exit 0.
- Pytest reports zero failures/errors against the migrated allowlisted MySQL database and Redis.

- [ ] **Step 3: Inspect final diff and repository contracts**

Run:

```bash
git diff --check
git status --short
rg -n "app\.main:app|poetry|postgres:13|command: arq|auth_secret.*Mapped" .github deploy scripts src/app src/scripts
```

Expected: `git diff --check` is silent; only intentional documentation/code/test changes are uncommitted; the obsolete runtime/storage patterns have no matches.

- [ ] **Step 4: Commit Task 6**

```bash
git add docs deploy
git commit -m "docs: record closed server foundation baseline"
```

## Plan Self-Review

- Spec coverage: Task 1 covers canonical deployment, lockfile CI, production example, and shared settings; Task 2 covers direct contracts; Task 3 covers mail crash recovery; Task 4 covers async asset boundaries; Task 5 covers readiness; Task 6 covers operations and the final gate.
- Type consistency: the health, Redis timeout, mail recovery settings, lease fields, recovery functions, and async asset wrapper names are identical across producers and consumers.
- Scope: no business-stage, RBAC, Admin asset-access, payment, timesheet, recruitment, or frontend behavior changes are included.
