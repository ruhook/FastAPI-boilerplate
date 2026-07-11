# Disable Central Authentication Rate Limit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the Redis-backed IP/account authentication counters from Web/Admin login and verification-code flows while preserving verification resend cooldowns, code expiry, and per-code attempt limits.

**Architecture:** Authentication routes will call their existing credential and verification services directly, without injecting Redis solely for login limiting or passing client IP values into verification services. Verification Redis remains responsible for code payloads, TTLs, resend cooldowns, and attempt counters. The central limiter module, its configuration, and its dedicated tests will be deleted rather than hidden behind a feature flag.

**Tech Stack:** Python 3.13, FastAPI, Pydantic Settings, redis-py asyncio, SQLAlchemy asyncio, pytest/pytest-asyncio, Ruff, mypy.

## Global Constraints

- Remove only centralized IP, identifier, and IP/identifier counters.
- Preserve the 60-second verification-code resend cooldown and its HTTP 429 `Retry-After` response.
- Preserve verification-code TTL and the five-attempt per-code limit.
- Keep unknown-account, wrong-password, and disabled-account authentication responses unchanged.
- Keep local virtual Admin authentication gated by `ENVIRONMENT=local` and `ENABLE_LOCAL_AUTH_BYPASS=true`.
- Do not delete existing `auth:rate-limit:*` Redis keys; let their current TTLs expire naturally.
- Do not add a replacement rate limiter or a database migration.
- Run database-writing tests only against `hr_server_codex_test`, never the local `hr_server` development database.

---

## File Map

- Create `tests/core/test_auth_without_central_rate_limit.py`: database-free regression contract for route/service signatures and preserved cooldown behavior.
- Modify `src/app/api/v1/login.py`: remove Web login limiter dependency and enforcement.
- Modify `src/app/admin/api/v1/auth.py`: remove Admin login limiter dependency and enforcement.
- Modify `src/app/api/v1/web_users.py`: stop calculating/passing client IP solely for verification limiting.
- Modify `src/app/modules/user/register_verification_service.py`: remove central limiter calls and `client_ip` parameters while preserving Redis code state.
- Delete `src/app/core/auth_rate_limit.py`: remove the unused HMAC/Lua limiter implementation.
- Modify `src/app/core/config.py`: remove central limiter settings and startup validation.
- Modify `src/app/core/exceptions/http_exceptions.py`: remove only `AuthRateLimitUnavailableException`; keep `TooManyRequestsException` for cooldowns.
- Modify `src/.env.example`: remove central limiter environment variables.
- Delete `tests/core/test_auth_rate_limit.py`: remove tests for the deleted implementation.
- Modify `tests/conftest.py`: remove suite-wide limiter threshold overrides.
- Modify `tests/core/test_security_config.py`: remove validation tests for deleted settings.
- Modify `tests/web/test_auth_abuse.py`: retain enumeration, SMTP, hash, cooldown, and attempt tests; remove assertions for centralized counters.
- Modify `docs/server-foundation-hardening-status-zh.md`: replace the stale “completed limiter” status with an explicit deferred-control note and link to the design.

---

### Task 1: Remove Central Limiting From Web and Admin Login

**Files:**
- Create: `tests/core/test_auth_without_central_rate_limit.py`
- Modify: `src/app/api/v1/login.py:1-52`
- Modify: `src/app/admin/api/v1/auth.py:1-65`

**Interfaces:**
- Consumes: existing `authenticate_user`, `login_admin_user`, `AdminLoginRequest`, and `UnauthorizedException`.
- Produces: `login_for_access_token(request, response, form_data, db)` and `admin_login(request, payload, db)` with no Redis dependency.

- [ ] **Step 1: Write database-free failing route tests**

Create `tests/core/test_auth_without_central_rate_limit.py` with route calls that intentionally omit the old Redis argument:

```python
from unittest.mock import AsyncMock

import pytest
from fastapi import Request, Response
from fastapi.security import OAuth2PasswordRequestForm

from src.app.admin.api.v1 import auth as admin_auth
from src.app.api.v1 import login as web_login
from src.app.core.exceptions.http_exceptions import UnauthorizedException
from src.app.modules.admin.admin_user.schema import AdminLoginRequest

pytestmark = [pytest.mark.no_database_cleanup, pytest.mark.asyncio]


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/login",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 50000),
        }
    )


async def test_web_login_reaches_authentication_without_rate_limit_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authenticate = AsyncMock(return_value=False)
    monkeypatch.setattr(web_login, "authenticate_user", authenticate)

    for _attempt in range(6):
        with pytest.raises(UnauthorizedException):
            await web_login.login_for_access_token(
                _request(),
                Response(),
                OAuth2PasswordRequestForm(
                    username="missing@example.com",
                    password="WrongPassword123!",
                ),
                AsyncMock(),
            )

    assert authenticate.await_count == 6


async def test_admin_login_reaches_authentication_without_rate_limit_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {"access_token": "token"}
    login = AsyncMock(return_value=expected)
    monkeypatch.setattr(admin_auth, "login_admin_user", login)

    for _attempt in range(6):
        result = await admin_auth.admin_login(
            _request(),
            AdminLoginRequest(
                username_or_email="missing-admin@example.com",
                password="WrongPassword123!",
            ),
            AsyncMock(),
        )

        assert result is expected

    assert login.await_count == 6
```

- [ ] **Step 2: Run the route tests and verify RED**

Run:

```bash
uv run pytest tests/core/test_auth_without_central_rate_limit.py -q
```

Expected: both tests fail with `TypeError` because the current route callables still require the `redis` argument.

- [ ] **Step 3: Remove Web login limiter wiring**

In `src/app/api/v1/login.py`:

- Remove `from redis.asyncio import Redis`.
- Remove the `auth_rate_limit` import.
- Remove the `async_get_redis` import.
- Remove `redis: Annotated[Redis, Depends(async_get_redis)]` from `login_for_access_token`.
- Delete the Web route's `await enforce_auth_rate_limit` block.
- Leave `Request` and `settings` because the route still records the user agent and sets cookie security.

The resulting start of the route must be:

```python
@router.post("/login", response_model=Token)
async def login_for_access_token(
    request: Request,
    response: Response,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> dict[str, str]:
    user = await authenticate_user(
        username_or_email=form_data.username,
        password=form_data.password,
        db=db,
    )
```

- [ ] **Step 4: Remove Admin login limiter wiring**

In `src/app/admin/api/v1/auth.py`:

- Remove `from redis.asyncio import Redis`.
- Remove the `auth_rate_limit` import.
- Remove the `async_get_redis` import.
- Remove `redis: Annotated[Redis, Depends(async_get_redis)]` from `admin_login`.
- Delete the Admin route's conditional `enforce_auth_rate_limit` block.
- Keep `is_local_dev_auto_login_admin` because refresh still uses it.

The resulting route must call authentication directly:

```python
@router.post("/login", response_model=AdminToken)
async def admin_login(
    request: Request,
    payload: AdminLoginRequest,
    db: Annotated[AsyncSession, Depends(async_get_db)],
) -> AdminToken:
    return await login_admin_user(
        payload=payload,
        db=db,
        all_permissions=ALL_ADMIN_PERMISSIONS,
        user_agent=request.headers.get("user-agent"),
    )
```

- [ ] **Step 5: Run the route tests and verify GREEN**

Run:

```bash
uv run pytest tests/core/test_auth_without_central_rate_limit.py -q
```

Expected: `2 passed`.

- [ ] **Step 6: Run focused static checks**

Run:

```bash
uv run ruff check src/app/api/v1/login.py src/app/admin/api/v1/auth.py tests/core/test_auth_without_central_rate_limit.py
```

Expected: exit 0 with no diagnostics.

- [ ] **Step 7: Commit the login change**

```bash
git add src/app/api/v1/login.py src/app/admin/api/v1/auth.py tests/core/test_auth_without_central_rate_limit.py
git commit -m "refactor: remove central login rate limit"
```

---

### Task 2: Remove Central Limiting From Verification Flows

**Files:**
- Modify: `tests/core/test_auth_without_central_rate_limit.py`
- Modify: `src/app/api/v1/web_users.py:1-215`
- Modify: `src/app/modules/user/register_verification_service.py:1-432`
- Modify: `tests/web/test_auth_abuse.py:1-270`

**Interfaces:**
- Consumes: verification Redis `get`, `set`, `delete`, and `ttl`; `TooManyRequestsException` for resend cooldown.
- Produces: `send_register_verification_code(email, redis, db)`, `send_password_reset_verification_code(email, redis, db)`, `verify_register_verification_code(email, code, redis)`, and `verify_password_reset_verification_code(email, code, redis)` without `client_ip`.

- [ ] **Step 1: Add failing service tests for the new signatures**

Append to `tests/core/test_auth_without_central_rate_limit.py`:

```python
import json
import time

from src.app.core.config import settings
from src.app.core.exceptions.http_exceptions import TooManyRequestsException
from src.app.modules.user import register_verification_service as verification_service


class FakeVerificationRedis:
    def __init__(self) -> None:
        self.storage: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    async def get(self, key: str) -> str | None:
        return self.storage.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> None:
        self.storage[key] = value
        self.expirations[key] = ex

    async def delete(self, key: str) -> None:
        self.storage.pop(key, None)
        self.expirations.pop(key, None)

    async def ttl(self, key: str) -> int:
        return self.expirations.get(key, -2)


async def test_verification_send_uses_redis_only_for_code_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeVerificationRedis()
    monkeypatch.setattr(
        verification_service.crud_users,
        "exists",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        verification_service,
        "_send_mail_sync",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        verification_service,
        "_generate_verification_code",
        lambda: "123456",
    )

    await verification_service.send_register_verification_code(
        email="candidate@example.com",
        redis=redis,  # type: ignore[arg-type]
        db=AsyncMock(),
    )

    key = verification_service._verification_cache_key("candidate@example.com")
    assert key in redis.storage


async def test_verification_check_uses_per_code_attempt_state_without_client_ip() -> None:
    redis = FakeVerificationRedis()
    email = "candidate@example.com"
    code = "123456"
    key = verification_service._verification_cache_key(email)
    redis.storage[key] = json.dumps(
        {
            "email": email,
            "code_hash": verification_service._hash_code(email, code),
            "sent_at": int(time.time()),
            "attempt_count": 0,
        }
    )
    redis.expirations[key] = 600

    await verification_service.verify_register_verification_code(
        email=email,
        code=code,
        redis=redis,  # type: ignore[arg-type]
    )

    assert key not in redis.storage


async def test_verification_resend_cooldown_still_returns_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeVerificationRedis()
    email = "candidate@example.com"
    key = verification_service._verification_cache_key(email)
    redis.storage[key] = json.dumps(
        {
            "email": email,
            "code_hash": "unused",
            "sent_at": int(time.time()),
            "attempt_count": 0,
        }
    )
    redis.expirations[key] = settings.CANDIDATE_REGISTER_VERIFICATION_CODE_TTL_SECONDS
    monkeypatch.setattr(
        verification_service.crud_users,
        "exists",
        AsyncMock(return_value=False),
    )

    with pytest.raises(TooManyRequestsException) as caught:
        await verification_service.send_register_verification_code(
            email=email,
            redis=redis,  # type: ignore[arg-type]
            db=AsyncMock(),
        )

    assert caught.value.status_code == 429
    assert int(caught.value.headers["Retry-After"]) > 0
```

- [ ] **Step 2: Run the expanded contract tests and verify RED**

Run:

```bash
uv run pytest tests/core/test_auth_without_central_rate_limit.py -q
```

Expected: the two login tests pass; the three verification tests fail with `TypeError` because `client_ip` is still required.

- [ ] **Step 3: Remove central limiter calls and `client_ip` parameters from the service**

In `src/app/modules/user/register_verification_service.py`:

- Remove the `auth_rate_limit` import.
- Remove `client_ip: str` from both send functions, `_verify_verification_code`, and both public verify wrappers.
- Delete the three `await enforce_auth_rate_limit` blocks.
- Remove `client_ip=client_ip` when wrappers call `_verify_verification_code`.
- Preserve both resend-cooldown `TooManyRequestsException` blocks exactly.
- Preserve `attempt_count`, TTL reuse, and code deletion behavior exactly.

The public interfaces must become:

```python
async def send_register_verification_code(
    *, email: str, redis: Redis, db: AsyncSession
) -> VerificationSendResult:


async def send_password_reset_verification_code(
    *, email: str, redis: Redis, db: AsyncSession
) -> VerificationSendResult:


async def verify_register_verification_code(
    *, email: str, code: str, redis: Redis
) -> None:


async def verify_password_reset_verification_code(
    *, email: str, code: str, redis: Redis
) -> None:
```

- [ ] **Step 4: Update Web user routes for the new service interfaces**

In `src/app/api/v1/web_users.py`:

- Remove `Request` from the FastAPI import.
- Remove `request: Request` from `register_user`, `send_register_code`, `send_password_reset_code`, and `confirm_password_reset`; none of these functions uses it after central limiting is removed.
- Remove all four `client_ip` keyword arguments passed to verification services.
- Keep each route's Redis dependency because code payloads, TTLs, cooldowns, and attempt counters still use Redis.

- [ ] **Step 5: Simplify the abuse-test fake and remove central-limit assertions**

In `tests/web/test_auth_abuse.py`:

- Rename `FakeRateLimitRedis` to `FakeVerificationRedis`.
- Remove `counts`, `keys`, and `eval` from the fake; retain `storage`, `expirations`, `get`, `set`, `delete`, and `ttl`.
- Rename `_install_fake_redis` parameters and return annotation to `FakeVerificationRedis`.
- Delete `_set_login_pair_limit`.
- Delete `test_web_login_enforces_ip_identifier_pair_limit` and `test_admin_login_enforces_ip_identifier_pair_limit`; Task 1's database-free tests own the login contract.
- Rename `test_local_virtual_admin_bypass_is_not_rate_limited` to `test_local_virtual_admin_login_succeeds_repeatedly`, remove fake Redis installation, and retain the two `200` assertions.
- Replace `test_verification_check_applies_ip_and_identifier_limits` with `test_verification_check_consumes_valid_code_without_central_counter`; remove rate-limit monkeypatches and `client_ip`, then assert the verification cache key was deleted.
- Keep enumeration, SMTP redaction, domain-separated hash, cooldown, and attempt-limit coverage.

- [ ] **Step 6: Run service and abuse tests and verify GREEN**

Run the database-free tests first:

```bash
uv run pytest tests/core/test_auth_without_central_rate_limit.py -q
```

Expected: `5 passed`.

Run database-writing abuse tests only against the dedicated test database:

```bash
env MYSQL_DB=hr_server_codex_test \
  TEST_DATABASE_NAME_ALLOWLIST=hr_server_codex_test \
  ALLOW_TEST_DATABASE_CLEANUP=true \
  uv run pytest tests/web/test_auth_abuse.py -q
```

Expected: all tests in the file pass; no test expects centralized IP/account 429 behavior.

- [ ] **Step 7: Run focused static checks**

Run:

```bash
uv run ruff check \
  src/app/api/v1/web_users.py \
  src/app/modules/user/register_verification_service.py \
  tests/core/test_auth_without_central_rate_limit.py \
  tests/web/test_auth_abuse.py
```

Expected: exit 0 with no diagnostics.

- [ ] **Step 8: Commit verification-flow changes**

```bash
git add \
  src/app/api/v1/web_users.py \
  src/app/modules/user/register_verification_service.py \
  tests/core/test_auth_without_central_rate_limit.py \
  tests/web/test_auth_abuse.py
git commit -m "refactor: remove central verification rate counters"
```

---

### Task 3: Delete Central Limiter Infrastructure and Configuration

**Files:**
- Delete: `src/app/core/auth_rate_limit.py`
- Delete: `tests/core/test_auth_rate_limit.py`
- Modify: `src/app/core/config.py:154-166,300-320`
- Modify: `src/app/core/exceptions/http_exceptions.py:20-34`
- Modify: `src/.env.example:33-44`
- Modify: `tests/conftest.py:51-73`
- Modify: `tests/core/test_security_config.py:98-115`

**Interfaces:**
- Consumes: Task 1 and Task 2 leave no production imports of `auth_rate_limit`.
- Produces: Settings with no `AUTH_RATE_LIMIT_PREFIX`, `AUTH_LOGIN_*`, or `AUTH_VERIFICATION_*` fields; `TooManyRequestsException` remains available for resend cooldowns.

- [ ] **Step 1: Add a failing absence contract for deleted settings**

Replace `test_auth_abuse_limits_must_be_positive` in `tests/core/test_security_config.py` with:

```python
def test_central_auth_rate_limit_settings_are_not_part_of_runtime_config() -> None:
    configured = Settings(_env_file=None, ENVIRONMENT="local")

    removed_names = {
        "AUTH_RATE_LIMIT_PREFIX",
        "AUTH_LOGIN_WINDOW_SECONDS",
        "AUTH_LOGIN_IP_LIMIT",
        "AUTH_LOGIN_IDENTIFIER_LIMIT",
        "AUTH_LOGIN_PAIR_LIMIT",
        "AUTH_VERIFICATION_SEND_WINDOW_SECONDS",
        "AUTH_VERIFICATION_SEND_IP_LIMIT",
        "AUTH_VERIFICATION_SEND_IDENTIFIER_LIMIT",
        "AUTH_VERIFICATION_CHECK_WINDOW_SECONDS",
        "AUTH_VERIFICATION_CHECK_IP_LIMIT",
        "AUTH_VERIFICATION_CHECK_IDENTIFIER_LIMIT",
    }

    assert removed_names.isdisjoint(type(configured).model_fields)
```

- [ ] **Step 2: Run the configuration test and verify RED**

Run:

```bash
uv run pytest tests/core/test_security_config.py::test_central_auth_rate_limit_settings_are_not_part_of_runtime_config -q
```

Expected: FAIL because the removed names are still present in `Settings.model_fields`.

- [ ] **Step 3: Remove limiter settings and validation**

In `src/app/core/config.py`:

- Delete the complete `AuthAbuseSettings` class.
- Remove `AuthAbuseSettings` from the `Settings` base-class list.
- Remove all `AUTH_LOGIN_*` and `AUTH_VERIFICATION_*` names from `positive_setting_names`.
- Leave verification business settings such as `CANDIDATE_REGISTER_VERIFICATION_RESEND_COOLDOWN_SECONDS` and `CANDIDATE_REGISTER_VERIFICATION_MAX_ATTEMPTS` unchanged.

In `src/.env.example`, delete the complete block from `AUTH_RATE_LIMIT_PREFIX` through `AUTH_VERIFICATION_CHECK_IDENTIFIER_LIMIT`.

In `tests/conftest.py`, delete `configure_test_rate_limit_defaults`; no suite-wide threshold override remains necessary.

- [ ] **Step 4: Delete unused limiter implementation and failure exception**

- Delete `src/app/core/auth_rate_limit.py`.
- Delete `tests/core/test_auth_rate_limit.py`.
- Delete only `AuthRateLimitUnavailableException` from `src/app/core/exceptions/http_exceptions.py`.
- Keep `TooManyRequestsException` unchanged so resend cooldowns retain status 429 and `Retry-After`.

- [ ] **Step 5: Run removal searches**

Run:

```bash
rg -n "AuthRateLimit|auth_rate_limit|AUTH_RATE_LIMIT|AUTH_LOGIN_(WINDOW|IP|IDENTIFIER|PAIR)|AUTH_VERIFICATION_(SEND|CHECK)_(WINDOW|IP|IDENTIFIER)" src tests --glob '!*.pyc'
```

Expected: no matches.

Run:

```bash
rg -n "TooManyRequestsException" src/app/modules/user/register_verification_service.py src/app/core/exceptions/http_exceptions.py
```

Expected: the exception definition and two resend-cooldown uses remain.

- [ ] **Step 6: Run configuration and contract tests**

Run:

```bash
uv run pytest \
  tests/core/test_security_config.py \
  tests/core/test_auth_without_central_rate_limit.py \
  -q
```

Expected: all tests pass.

- [ ] **Step 7: Run static checks for the cleaned infrastructure**

Run:

```bash
uv run ruff check \
  src/app/core/config.py \
  src/app/core/exceptions/http_exceptions.py \
  src/app/api/v1/login.py \
  src/app/admin/api/v1/auth.py \
  src/app/api/v1/web_users.py \
  src/app/modules/user/register_verification_service.py \
  tests/conftest.py \
  tests/core/test_security_config.py \
  tests/core/test_auth_without_central_rate_limit.py \
  tests/web/test_auth_abuse.py
```

Expected: exit 0 with no diagnostics.

- [ ] **Step 8: Commit infrastructure deletion**

```bash
git add -A \
  src/app/core/auth_rate_limit.py \
  src/app/core/config.py \
  src/app/core/exceptions/http_exceptions.py \
  src/.env.example \
  tests/conftest.py \
  tests/core/test_auth_rate_limit.py \
  tests/core/test_security_config.py
git commit -m "refactor: delete central auth rate limiter"
```

---

### Task 4: Update Status Documentation and Verify the Complete Change

**Files:**
- Modify: `docs/server-foundation-hardening-status-zh.md:1-28`
- Verify: `docs/superpowers/specs/2026-07-11-disable-central-auth-rate-limit-design.md`

**Interfaces:**
- Consumes: completed route, service, and infrastructure removal from Tasks 1-3.
- Produces: repository documentation that no longer claims centralized authentication limiting is active and preserves the future restoration checklist.

- [ ] **Step 1: Correct the hardening status document**

In `docs/server-foundation-hardening-status-zh.md`:

- Change the update date to `2026-07-11`.
- Remove the completed bullet claiming Web/Admin and verification Redis atomic rate limiting is active.
- Add this item under `仍需继续处理`:

```markdown
- 登录、验证码发送与校验的集中式 IP/账号限流已在当前开发阶段暂时移除；验证码重发冷却、有效期和单码尝试上限仍保留。最终恢复要求见 [暂时移除集中式认证限流设计](superpowers/specs/2026-07-11-disable-central-auth-rate-limit-design.md)。
```

- Remove or update the CI sentence that says limiter boundary tests are active; keep the statement about isolated MySQL/Redis and explicit cleanup permission.
- Update the closing judgment so it does not claim all password/permission security boundaries are closed while centralized login abuse protection is deferred.

- [ ] **Step 2: Verify documentation and source consistency**

Run:

```bash
rg -n "已接入 Redis 原子限流|限流边界由|AUTH_LOGIN_IP_LIMIT|AUTH_RATE_LIMIT_PREFIX" \
  docs/server-foundation-hardening-status-zh.md \
  src/.env.example \
  src/app \
  tests
```

Expected: no matches.

Run:

```bash
git diff --check
```

Expected: exit 0.

- [ ] **Step 3: Run the focused regression suite**

Run database-free tests:

```bash
uv run pytest \
  tests/core/test_auth_without_central_rate_limit.py \
  tests/core/test_security_config.py \
  -q
```

Expected: all tests pass.

Run authentication/verification integration tests only against the dedicated test database:

```bash
env MYSQL_DB=hr_server_codex_test \
  TEST_DATABASE_NAME_ALLOWLIST=hr_server_codex_test \
  ALLOW_TEST_DATABASE_CLEANUP=true \
  uv run pytest \
    tests/web/test_auth_abuse.py \
    tests/web/test_auth.py \
    tests/admin/test_auth.py \
    -q
```

Expected: all selected tests pass with no centralized-limit assertions.

- [ ] **Step 4: Run final lint and type checks**

Run:

```bash
uv run ruff check src tests
```

Expected: exit 0 with no diagnostics.

Run:

```bash
uv run mypy \
  src/app/core/config.py \
  src/app/api/v1/login.py \
  src/app/admin/api/v1/auth.py \
  src/app/api/v1/web_users.py \
  src/app/modules/user/register_verification_service.py
```

Expected: exit 0 with no type errors.

- [ ] **Step 5: Commit documentation correction**

```bash
git add docs/server-foundation-hardening-status-zh.md
git commit -m "docs: defer centralized auth rate limiting"
```

- [ ] **Step 6: Confirm only intended changes remain**

Run:

```bash
git status --short
git diff --stat 49c4715..HEAD
git log -6 --oneline
```

Expected: the worktree is clean; the plan plus three implementation commits and the documentation correction are visible; no frontend or unrelated server files changed.

---

## Completion Criteria

- Web/Admin login callables have no limiter Redis dependency and repeated attempts never become centralized-limit 429 responses.
- Verification services no longer accept `client_ip` or call a centralized limiter.
- Resend cooldown still returns 429 with `Retry-After`; code expiry and five-attempt behavior remain intact.
- `src/app/core/auth_rate_limit.py`, its settings, its unavailable exception, and its dedicated tests are absent.
- Existing limiter keys are left to expire naturally.
- Status documentation explicitly marks centralized authentication abuse protection as deferred and links to the restoration checklist.
- Focused tests, Ruff, mypy, removal searches, and `git diff --check` pass.
