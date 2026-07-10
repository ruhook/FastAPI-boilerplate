# Server Security Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make production configuration fail closed, remove passwordless local-admin authentication, redact secrets from request logs, encrypt and stop returning SMTP credentials, and make candidate presentation fall back safely for inconsistent state.

**Architecture:** Keep the current FastAPI modular monolith. Add focused security helpers under `src/app/core`, validate unsafe production combinations in `Settings`, keep credential encryption behind a small versioned interface, and use a forward-only Alembic migration that supports a compatibility window for existing plaintext SMTP credentials.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic Settings v2, SQLAlchemy 2 async, Alembic, MySQL, `cryptography.fernet`, pytest, Ruff, mypy.

## Global Constraints

- Work on the current branch; do not create a branch or worktree.
- Do not stage or overwrite concurrent user changes in `src/scripts/run_candidate_my_jobs_demo.py` or `tests/scripts/test_candidate_portal_demo_data.py`.
- Preserve existing Web/Admin API paths.
- Existing sessions may be invalidated by later authentication work, but this plan does not implement refresh sessions yet.
- Mail-account read responses must never include `auth_secret`.
- Existing SMTP credentials may be migrated or re-entered; new writes must always be encrypted.
- Production startup must reject unsafe configuration instead of silently falling back.
- Every production behavior change follows red-green-refactor and is committed independently.

---

### Task 1: Fail-closed settings and explicit local administrator bootstrap

**Files:**
- Create: `tests/core/test_security_config.py`
- Modify: `src/app/core/config.py:17-270`
- Modify: `src/app/core/setup.py:214-223`
- Modify: `src/app/admin/local_admin_bootstrap.py:16-96`
- Modify: `src/app/modules/admin/admin_user/service.py:50-91,423-450`
- Modify: `src/app/admin/api/dependencies.py:42-82`
- Modify: `src/app/admin/api/v1/auth.py:39-55`
- Modify: `tests/admin/test_auth.py:13-73`
- Modify: `src/.env.example`

**Interfaces:**
- Produces: `Settings.validate_runtime_security() -> None`.
- Produces: `Settings.ENABLE_LOCAL_ADMIN_BOOTSTRAP: bool` with default `False`.
- Produces: `Settings.CORS_ALLOW_CREDENTIALS: bool` with default `True`.
- Produces: `should_ensure_local_admin(settings: Settings) -> bool`.
- Removes: virtual `HaokangImport` authentication that accepts arbitrary passwords.

- [ ] **Step 1: Write failing production-configuration tests**

Add `tests/core/test_security_config.py` with table-driven tests using `Settings(_env_file=None, ...)`. The tests must establish these exact behaviors:

```python
import pytest
from pydantic import ValidationError

from src.app.core.config import EnvironmentOption, Settings


def production_settings(**overrides):
    values = {
        "ENVIRONMENT": EnvironmentOption.PRODUCTION,
        "SECRET_KEY": "production-secret-with-at-least-32-characters",
        "CORS_ORIGINS": ["https://admin.example.com"],
        "CORS_ALLOW_CREDENTIALS": True,
        "ENABLE_LOCAL_ADMIN_BOOTSTRAP": False,
        "CANDIDATE_REGISTER_VERIFICATION_ENABLED": False,
        "ASSET_STORAGE_PROVIDER": "local",
        "MAIL_CREDENTIAL_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_rejects_placeholder_secret_key():
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        production_settings(SECRET_KEY="secret-key")


def test_production_rejects_credentialed_wildcard_cors():
    with pytest.raises(ValidationError, match="CORS"):
        production_settings(CORS_ORIGINS=["*"], CORS_ALLOW_CREDENTIALS=True)


def test_production_rejects_local_admin_bootstrap():
    with pytest.raises(ValidationError, match="ENABLE_LOCAL_ADMIN_BOOTSTRAP"):
        production_settings(ENABLE_LOCAL_ADMIN_BOOTSTRAP=True)


def test_production_requires_mail_credential_encryption_key():
    with pytest.raises(ValidationError, match="MAIL_CREDENTIAL_ENCRYPTION_KEY"):
        production_settings(MAIL_CREDENTIAL_ENCRYPTION_KEY="")


def test_local_admin_bootstrap_is_opt_in():
    settings = Settings(_env_file=None, ENVIRONMENT="local")
    assert settings.ENABLE_LOCAL_ADMIN_BOOTSTRAP is False
```

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
.venv/bin/pytest -q tests/core/test_security_config.py
```

Expected: collection or assertion failures because the new settings and validation do not exist.

- [ ] **Step 3: Implement runtime validation and safe CORS configuration**

In `config.py`, add `model_validator` and the following settings:

```python
class LocalDevelopmentSettings(BaseSettings):
    ENABLE_LOCAL_ADMIN_BOOTSTRAP: bool = False


class MailCredentialSettings(BaseSettings):
    MAIL_CREDENTIAL_ENCRYPTION_KEY: SecretStr = SecretStr("")


class CORSSettings(BaseSettings):
    CORS_ORIGINS: list[str] = ["*"]
    CORS_METHODS: list[str] = ["*"]
    CORS_HEADERS: list[str] = ["*"]
    CORS_ALLOW_CREDENTIALS: bool = True
```

Add both new setting classes to `Settings`. Add an `after` model validator that returns immediately outside production and otherwise raises `ValueError` for placeholder/short secrets, credentialed wildcard CORS, enabled local bootstrap, incomplete enabled verification SMTP, incomplete OSS configuration, or missing credential encryption key when mail delivery is configured for real delivery. Remove real-looking candidate SMTP defaults and replace them with empty strings.

In `core/setup.py`, pass `settings.CORS_ALLOW_CREDENTIALS` to `CORSMiddleware`; only use `allow_origin_regex=".*"` when credentials are disabled.

- [ ] **Step 4: Write failing local-admin tests**

Replace the passwordless virtual-admin test with pure behavior tests:

```python
def test_local_admin_bootstrap_requires_explicit_flag():
    settings = Settings(_env_file=None, ENVIRONMENT="local", ENABLE_LOCAL_ADMIN_BOOTSTRAP=False)
    assert should_ensure_local_admin(settings) is False


def test_local_admin_bootstrap_can_be_explicitly_enabled_locally():
    settings = Settings(_env_file=None, ENVIRONMENT="local", ENABLE_LOCAL_ADMIN_BOOTSTRAP=True)
    assert should_ensure_local_admin(settings) is True


def test_local_admin_bootstrap_never_runs_in_production():
    settings = production_settings(ENABLE_LOCAL_ADMIN_BOOTSTRAP=False)
    assert should_ensure_local_admin(settings) is False
```

Update the API test to assert that `HaokangImport` with an arbitrary password returns `401` when no matching database account exists.

- [ ] **Step 5: Run local-admin tests and verify red**

Run:

```bash
.venv/bin/pytest -q tests/core/test_security_config.py tests/admin/test_auth.py -k "local or bootstrap or dev_auto"
```

Expected: failures showing bootstrap depends only on environment and virtual authentication still succeeds.

- [ ] **Step 6: Remove virtual auth and stop resetting local passwords**

Change `should_ensure_local_admin` to accept `Settings` and require both local environment and the explicit flag. `ensure_local_admin_user` may create a missing account, but when the account exists it must not replace `hashed_password`. Remove `is_local_dev_auto_login_admin`, `build_local_dev_auto_login_admin`, and `issue_local_dev_auto_login_admin_tokens`; remove their branches from login, refresh, and current-user dependencies.

Update `src/.env.example` with:

```env
ENABLE_LOCAL_ADMIN_BOOTSTRAP=false
CORS_ALLOW_CREDENTIALS=true
MAIL_CREDENTIAL_ENCRYPTION_KEY=""
```

- [ ] **Step 7: Verify task green**

Run:

```bash
.venv/bin/pytest -q tests/core/test_security_config.py tests/admin/test_auth.py -k "local or bootstrap or dev_auto"
.venv/bin/ruff check --no-fix src/app/core/config.py src/app/core/setup.py src/app/admin/local_admin_bootstrap.py src/app/modules/admin/admin_user/service.py src/app/admin/api/dependencies.py src/app/admin/api/v1/auth.py tests/core/test_security_config.py tests/admin/test_auth.py
```

Expected: all selected tests and Ruff checks pass.

- [ ] **Step 8: Commit task without staging user-owned demo changes**

```bash
git add src/.env.example src/app/core/config.py src/app/core/setup.py src/app/admin/local_admin_bootstrap.py src/app/modules/admin/admin_user/service.py src/app/admin/api/dependencies.py src/app/admin/api/v1/auth.py tests/core/test_security_config.py tests/admin/test_auth.py
git commit -m "fix: fail closed on unsafe server configuration"
```

---

### Task 2: Central request-log redaction

**Files:**
- Create: `src/app/core/log_redaction.py`
- Create: `tests/core/test_log_redaction.py`
- Modify: `src/app/core/exception_logging.py:1-106`
- Modify: `src/app/middleware/logger_middleware.py:1-70`

**Interfaces:**
- Produces: `redact_sensitive_data(value: Any) -> Any`.
- Produces: `serialize_request_body_for_log(request: Request, max_length: int = 4000) -> Awaitable[str | None]`.
- Produces: `redact_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]`.

- [ ] **Step 1: Write failing redaction tests**

Create tests covering nested mappings, list values, case/format normalization, URL-encoded bodies, malformed JSON, query parameters, and output bounds:

```python
def test_redacts_nested_secret_aliases():
    source = {
        "password": "candidate-pass",
        "profile": {"refreshToken": "refresh-value"},
        "accounts": [{"auth_secret": "smtp-code", "email": "mail@example.com"}],
    }
    assert redact_sensitive_data(source) == {
        "password": "[REDACTED]",
        "profile": {"refreshToken": "[REDACTED]"},
        "accounts": [{"auth_secret": "[REDACTED]", "email": "mail@example.com"}],
    }


def test_malformed_json_is_not_logged_raw():
    request = build_request(b'{"password":"visible"', "application/json")
    assert await serialize_request_body_for_log(request) == "<malformed json body omitted>"


def test_urlencoded_password_is_redacted():
    request = build_request(b"username=alice&password=visible", "application/x-www-form-urlencoded")
    assert await serialize_request_body_for_log(request) == "username=alice&password=%5BREDACTED%5D"
```

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
.venv/bin/pytest -q tests/core/test_log_redaction.py
```

Expected: import failure because `log_redaction` does not exist.

- [ ] **Step 3: Implement the shared helper**

Normalize keys by removing non-alphanumeric characters and lowercasing. Redact when the normalized key is one of the exact secret aliases or ends with `password`, `token`, `secret`, or `verificationcode`. Recursively process mappings and lists. Parse JSON with `json.loads`; parse forms with `urllib.parse.parse_qsl`; omit multipart and unsupported bodies; never fall back to raw text for a parse failure.

- [ ] **Step 4: Replace duplicate logging serializers**

Delete the local `_serialize_request_body` functions from both logging modules. Import and use `serialize_request_body_for_log`. Pass `request.query_params` through `redact_mapping` before logging. Preserve request id, path params, status, and body length behavior.

- [ ] **Step 5: Verify task green**

Run:

```bash
.venv/bin/pytest -q tests/core/test_log_redaction.py
.venv/bin/ruff check --no-fix src/app/core/log_redaction.py src/app/core/exception_logging.py src/app/middleware/logger_middleware.py tests/core/test_log_redaction.py
```

Expected: all tests and Ruff checks pass.

- [ ] **Step 6: Commit task**

```bash
git add src/app/core/log_redaction.py src/app/core/exception_logging.py src/app/middleware/logger_middleware.py tests/core/test_log_redaction.py
git commit -m "fix: redact secrets from request logs"
```

---

### Task 3: Encrypt and stop returning SMTP credentials

**Files:**
- Create: `src/app/core/credential_crypto.py`
- Create: `tests/core/test_credential_crypto.py`
- Create: `src/migrations/versions/20260710_000040_mail_credential_encryption.py`
- Create: `src/scripts/encrypt_mail_account_credentials.py`
- Create: `tests/scripts/test_encrypt_mail_account_credentials.py`
- Modify: `pyproject.toml:9-37`
- Modify: `uv.lock`
- Modify: `src/app/modules/admin/mail_account/model.py:10-24`
- Modify: `src/app/modules/admin/mail_account/schema.py:21-148`
- Modify: `src/app/modules/admin/mail_account/service.py:17-153`
- Modify: `src/app/modules/admin/mail_task/service.py:478-540`
- Modify: `tests/admin/mail/test_accounts.py`

**Interfaces:**
- Produces: `encrypt_credential(secret: str, key: SecretStr | str | None = None) -> str` returning `v1:<fernet-token>`.
- Produces: `decrypt_credential(value: str, key: SecretStr | str | None = None) -> str`.
- Produces: `resolve_mail_account_auth_secret(account: MailAccount) -> str` preferring encrypted storage and temporarily falling back to legacy plaintext.
- Changes: `MailAccountRead.has_auth_secret: bool`; removes `MailAccountRead.auth_secret`.
- Adds: nullable `mail_account.auth_secret_encrypted` and makes legacy `auth_secret` nullable.

- [ ] **Step 1: Add direct cryptography dependency**

Add `"cryptography>=45.0.0,<46"` to project dependencies and run:

```bash
uv lock
```

Expected: `pyproject.toml` and `uv.lock` both record the direct dependency.

- [ ] **Step 2: Write failing credential-crypto tests**

```python
from cryptography.fernet import Fernet
import pytest

from src.app.core.credential_crypto import CredentialDecryptionError, decrypt_credential, encrypt_credential


def test_credential_ciphertext_is_versioned_and_round_trips():
    key = Fernet.generate_key().decode()
    encrypted = encrypt_credential("smtp-code", key)
    assert encrypted.startswith("v1:")
    assert "smtp-code" not in encrypted
    assert decrypt_credential(encrypted, key) == "smtp-code"


def test_credential_tampering_is_rejected():
    key = Fernet.generate_key().decode()
    encrypted = encrypt_credential("smtp-code", key)
    with pytest.raises(CredentialDecryptionError):
        decrypt_credential(encrypted + "tampered", key)
```

- [ ] **Step 3: Run tests and verify red**

Run:

```bash
.venv/bin/pytest -q tests/core/test_credential_crypto.py
```

Expected: import failure because the crypto module does not exist.

- [ ] **Step 4: Implement versioned authenticated encryption**

Use `cryptography.fernet.Fernet`. Resolve the default key from `settings.MAIL_CREDENTIAL_ENCRYPTION_KEY`, reject an empty or malformed key with a stable configuration error, prefix ciphertext with `v1:`, and translate `InvalidToken` into `CredentialDecryptionError` without logging ciphertext or plaintext.

- [ ] **Step 5: Write failing schema and service tests**

Extend `tests/admin/mail/test_accounts.py` to assert every create/list/detail/update response has `has_auth_secret is True` and does not contain `auth_secret`. Add a service-level test that inspects the persisted row and asserts plaintext is absent from `auth_secret_encrypted` and the legacy column is `None` for new writes.

- [ ] **Step 6: Run mail-account tests and verify red**

Run with the existing disposable local database configuration:

```bash
ALLOW_TEST_DATABASE_CLEANUP=true .venv/bin/pytest -q tests/admin/mail/test_accounts.py
```

Expected: response assertions fail because the current read schema exposes the secret and encrypted persistence does not exist.

- [ ] **Step 7: Add migration, model, schema, and service behavior**

The migration must:

```python
revision = "20260710_000040"
down_revision = "20260628_000039"

def upgrade():
    op.add_column("mail_account", sa.Column("auth_secret_encrypted", sa.String(length=1024), nullable=True))
    op.alter_column("mail_account", "auth_secret", existing_type=sa.String(length=255), nullable=True)

def downgrade():
    # Refuse downgrade when encrypted-only rows exist; otherwise restore non-null plaintext and drop the new column.
```

Refactor schemas so create requires `auth_secret`, update accepts an optional value, and reads expose only `has_auth_secret`. New create/update writes set `auth_secret_encrypted=encrypt_credential(value)` and `auth_secret=None`. `resolve_mail_account_auth_secret` decrypts the encrypted value, otherwise temporarily returns a non-empty legacy value.

Update SMTP sending to call `resolve_mail_account_auth_secret(account)` instead of reading `account.auth_secret` directly.

- [ ] **Step 8: Add and test the migration command**

The command queries rows with non-empty plaintext and empty encrypted value, encrypts each secret, clears plaintext, commits once, and prints only migrated/skipped counts. Its pure helper accepts a list of account models and an encryption callable so tests prove it never includes secrets in its result or output.

Run:

```bash
.venv/bin/pytest -q tests/core/test_credential_crypto.py tests/scripts/test_encrypt_mail_account_credentials.py
ALLOW_TEST_DATABASE_CLEANUP=true .venv/bin/pytest -q tests/admin/mail/test_accounts.py
```

Expected: all tests pass.

- [ ] **Step 9: Verify migration and lint**

Run:

```bash
cd src && ../.venv/bin/alembic -c alembic.ini heads
cd ..
.venv/bin/ruff check --no-fix src/app/core/credential_crypto.py src/app/modules/admin/mail_account src/app/modules/admin/mail_task/service.py src/scripts/encrypt_mail_account_credentials.py src/migrations/versions/20260710_000040_mail_credential_encryption.py tests/core/test_credential_crypto.py tests/admin/mail/test_accounts.py tests/scripts/test_encrypt_mail_account_credentials.py
```

Expected: exactly `20260710_000040 (head)` and Ruff passes.

- [ ] **Step 10: Commit task**

```bash
git add pyproject.toml uv.lock src/app/core/credential_crypto.py src/app/modules/admin/mail_account/model.py src/app/modules/admin/mail_account/schema.py src/app/modules/admin/mail_account/service.py src/app/modules/admin/mail_task/service.py src/migrations/versions/20260710_000040_mail_credential_encryption.py src/scripts/encrypt_mail_account_credentials.py tests/core/test_credential_crypto.py tests/admin/mail/test_accounts.py tests/scripts/test_encrypt_mail_account_credentials.py
git commit -m "fix: encrypt stored mail credentials"
```

---

### Task 4: Candidate presentation safe fallback

**Files:**
- Modify: `tests/modules/test_candidate_presentation.py`
- Modify: `src/app/modules/job_progress/candidate_presentation.py:170-286`

**Interfaces:**
- Preserves: `build_candidate_presentation(...) -> CandidatePresentation`.
- Changes: unknown stages and incomplete stage evidence return the safe application-review presentation.

- [ ] **Step 1: Write failing regression tests**

```python
def test_unknown_stage_with_stale_contract_data_falls_back_safely():
    result = build_candidate_presentation(
        current_stage="unexpected_stage",
        assessment_enabled=True,
        process_data={},
        contract_data={"draft_contract_attachment": {"asset_id": 1}},
    )
    assert (result["candidate_status"], result["candidate_stage"], result["candidate_action"]) == (
        "under_review", "application_review", "view_details"
    )


def test_assessment_review_without_submission_evidence_falls_back_safely():
    result = build_candidate_presentation(
        current_stage="assessment_review",
        assessment_enabled=True,
        process_data={},
        contract_data=None,
    )
    assert result["candidate_stage"] == "application_review"
    assert result["candidate_action"] == "view_details"
```

- [ ] **Step 2: Run tests and verify red**

Run:

```bash
.venv/bin/pytest -q tests/modules/test_candidate_presentation.py
```

Expected: the two new tests fail with `upload_contract` and `view_status` respectively.

- [ ] **Step 3: Implement stage-first validation**

Define the supported stage set and return application review before inspecting contract data when the stage is unsupported. For `assessment_review`, require assessment attachment/submission evidence or explicit revision evidence before returning assessment presentation. Limit contract actions to `screening_passed` and `contract_pool`; active task-group presentation remains evaluated before contract upload rules.

- [ ] **Step 4: Verify task green**

Run:

```bash
.venv/bin/pytest -q tests/modules/test_candidate_presentation.py tests/web/test_my_applications.py
.venv/bin/ruff check --no-fix src/app/modules/job_progress/candidate_presentation.py tests/modules/test_candidate_presentation.py
```

Expected: all tests and Ruff pass.

- [ ] **Step 5: Commit only presentation files**

```bash
git add src/app/modules/job_progress/candidate_presentation.py tests/modules/test_candidate_presentation.py
git commit -m "fix: fail safely for inconsistent candidate state"
```

---

### Task 5: Security-baseline documentation and verification gate

**Files:**
- Modify: `README.md:166-206`
- Modify: `docs/deployment-zh.md:60-90,209-226`
- Modify: `docs/development-minimal-zh.md`
- Modify: `.github/workflows/linting.yml`
- Test: all tests and checks named below

**Interfaces:**
- Documents exact environment variables and rollout commands.
- Makes lint verification non-mutating with `ruff check --no-fix`.

- [ ] **Step 1: Document the new security contract**

Document:

- production `SECRET_KEY` requirements and explicit CORS origins;
- `ENABLE_LOCAL_ADMIN_BOOTSTRAP=false` in production;
- generating a Fernet key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`;
- running `python -m src.scripts.encrypt_mail_account_credentials` after the migration;
- rotating the previously committed candidate SMTP credential;
- mail-account API responses no longer returning authorization codes.

- [ ] **Step 2: Make lint CI non-mutating**

Change the workflow command to:

```yaml
- name: Run Ruff
  run: uv run ruff check --no-fix src tests
```

- [ ] **Step 3: Run focused security regression tests**

```bash
.venv/bin/pytest -q tests/core/test_security_config.py tests/core/test_log_redaction.py tests/core/test_credential_crypto.py tests/modules/test_candidate_presentation.py tests/scripts/test_encrypt_mail_account_credentials.py
ALLOW_TEST_DATABASE_CLEANUP=true .venv/bin/pytest -q tests/admin/test_auth.py tests/admin/mail/test_accounts.py
```

Expected: all selected tests pass.

- [ ] **Step 4: Run repository verification appropriate to this subproject**

```bash
.venv/bin/python -m compileall -q src tests
.venv/bin/ruff check --no-fix src/app/core src/app/admin/local_admin_bootstrap.py src/app/admin/api/dependencies.py src/app/admin/api/v1/auth.py src/app/modules/admin/admin_user/service.py src/app/modules/admin/mail_account src/app/modules/admin/mail_task/service.py src/app/modules/job_progress/candidate_presentation.py src/scripts/encrypt_mail_account_credentials.py tests/core tests/admin/test_auth.py tests/admin/mail/test_accounts.py tests/modules/test_candidate_presentation.py tests/scripts/test_encrypt_mail_account_credentials.py
cd src && ../.venv/bin/alembic -c alembic.ini heads
cd .. && git diff --check
```

Expected: compile and Ruff exit zero, Alembic reports one `20260710_000040` head, and `git diff --check` is clean.

- [ ] **Step 5: Confirm user-owned concurrent changes remain unstaged**

Run:

```bash
git status --short
git diff -- src/scripts/run_candidate_my_jobs_demo.py tests/scripts/test_candidate_portal_demo_data.py
```

Expected: any pre-existing user edits remain present and are not part of the security-baseline commits.

- [ ] **Step 6: Commit docs and CI**

```bash
git add README.md docs/deployment-zh.md docs/development-minimal-zh.md .github/workflows/linting.yml
git commit -m "docs: document server security baseline"
```

## Plan Self-Review

- Security-baseline spec coverage: production validation, local bootstrap, CORS, redaction, SMTP encryption/write-only responses, candidate safe fallback, migration, docs, and focused gates all have explicit tasks.
- Deferred by design: revocable sessions, transactional outbox, recruitment concurrency, asset storage, strict RBAC, SQL pagination, and full-repository type cleanup belong to later subproject plans defined in the umbrella design.
- Type consistency: `MAIL_CREDENTIAL_ENCRYPTION_KEY`, `auth_secret_encrypted`, `has_auth_secret`, `encrypt_credential`, `decrypt_credential`, and `resolve_mail_account_auth_secret` use the same names across tasks.
- Worktree safety: no task stages the two concurrently modified demo files.
