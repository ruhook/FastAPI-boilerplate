# Server Foundation Closure Design

## Goal

Finish the remaining server-foundation work before business-layer refactoring. The existing FastAPI modular monolith, Web/Admin split, MySQL database, Redis event transport, transactional outbox, and Supervisor deployment remain in place. This is a bounded closure pass, not an architecture rewrite.

## Scope And Constraints

- Work on the current `codex/candidate-applications-state-contract` branch. Do not create another branch or worktree.
- Do not change recruitment, payment, timesheet, talent, or other business semantics.
- Preserve the intentional rule that every authenticated Admin account may access Admin asset endpoints globally.
- Remove compatibility code instead of maintaining dual paths. In particular, plaintext SMTP credentials and the duplicate Web logout route are not retained.
- Do not add a new infrastructure service.
- Use test-first implementation for runtime behavior changes.
- Keep Alembic history forward-only and maintain a single migration head.

## Approaches Considered

1. **One bounded closure pass (chosen):** fix the five known foundation gaps independently, with a test gate after each one. This leaves one coherent baseline for the upcoming business refactor.
2. **Split runtime and deployment work into separate passes:** lowers per-commit scope but leaves two competing operational baselines while business refactoring starts.
3. **Fix only mail and asset runtime reliability:** fastest, but deliberately leaves dead routes, plaintext fallback, broken deployment examples, and weak readiness in the foundation.

The first approach is chosen because the remaining work is small, independently testable, and all five items affect the reliability of the shared foundation.

## 1. Canonical Deployment And Dependency Installation

The only supported runtime processes are:

- `src.app.main_web:app`
- `src.app.main_admin:app`
- `event_consumer.py`

Supervisor and Nginx examples under `deploy/` remain the canonical production deployment. The obsolete Docker examples under `scripts/local_with_uvicorn`, `scripts/gunicorn_managing_uvicorn_workers`, and `scripts/production_with_nginx` are removed instead of maintained. They currently reference an empty `app.main:app`, Poetry, PostgreSQL, and an ARQ worker that are no longer part of this server. The empty `src/app/main.py` entrypoint is removed as well.

All GitHub workflows install from `uv.lock` with frozen resolution. CI uses `uv sync --frozen --all-extras --all-groups`, followed by `uv run --frozen ...`, so tests, lint, type checks, and migrations execute against the same dependency graph.

The production environment example is synchronized with current settings. It documents all required secrets and service settings, uses 15-minute access-token defaults, disables local-only features, and includes complete event, mail, verification, storage, and health-check settings. Secret placeholders remain deliberately invalid so copied production configuration fails closed until the operator replaces them.

## 2. Recoverable Mail Processing

`MailTask` gains explicit processing timing fields:

- `processing_started_at`: when the current processing attempt was claimed.
- `processing_lease_expires_at`: when an unfinished attempt becomes recoverable.

Claiming a `pending` or `retrying` task sets both fields before rendering. Reaching a terminal state clears the lease fields. The lease is long enough to exceed the existing bounded SMTP timeout and is configured through positive settings.

The event-consumer process runs a small periodic recovery loop using the existing database and outbox:

- stale `rendering` tasks become `retrying` and enqueue a new mail-task event in the same transaction;
- stale `sending` tasks in SMTP mode become `delivery_unknown`, because the process may have exited before or after the external SMTP server accepted the message;
- stale `sending` tasks in preview mode become `retrying`, because preview delivery has no external side effect.

Recovery locks selected tasks, processes rows in deterministic id order, and is safe to run from more than one consumer. A recovered SMTP task is never automatically resent from an ambiguous state.

## 3. Non-Blocking Asset Storage And Archive Work

The synchronous `oss2`, local filesystem, document conversion, and ZIP compression functions remain synchronous internally, but every call from an async request or event handler is executed through `asyncio.to_thread`.

The asset service exposes async storage boundaries for write, read, and compensation delete. Mail attachment loading uses the same offloaded boundary. Admin ZIP generation performs PDF conversion, archive writes, archive finalization, and spool seek outside the event loop while retaining the existing file-count, upload-size, aggregate-byte, safe-name, and response-header rules.

The design intentionally does not add multipart object streaming or a new storage abstraction. Existing request limits cap memory and disk usage; this pass only removes event-loop blocking without changing API contracts.

## 4. Direct Contracts Without Compatibility Paths

The Web API registers exactly one `POST /api/v1/logout`: the implementation that revokes the refresh session and deletes the refresh cookie. The cookie-only duplicate module is removed.

Mail accounts read credentials only from `auth_secret_encrypted`. The `auth_secret` model field and runtime fallback are removed. A forward Alembic migration drops the plaintext database column. The one-off plaintext-to-encrypted migration command and its compatibility tests/documentation are removed because this development-stage server does not preserve plaintext credential compatibility.

Existing encrypted credentials continue to work. Rows without an encrypted credential are treated as unconfigured and must be updated through the existing Mail Account API.

## 5. Bounded Readiness Checks

Liveness remains process-only and does not contact dependencies. Readiness checks application initialization, MySQL, and Redis.

Database readiness uses a dedicated engine connection rather than the normal request transaction dependency, so the check never enters the request commit path. Database and Redis probes run concurrently. Each probe has an explicit timeout and converts timeout/connection failures into an `unhealthy` result with HTTP 503. The public response remains stable and does not expose exception text or credentials.

The Redis connection pool is created with bounded connect and socket timeouts. The readiness timeout settings are positive and production-safe by default.

## Error Handling And Observability

- Mail recovery logs task id, previous status, recovered status, and recovery reason without recipient bodies or credentials.
- Asset offload failures retain the current public exceptions and compensation cleanup behavior.
- Readiness logs dependency failures server-side and returns only healthy/unhealthy states.
- Deployment examples must fail closed when required production secrets are placeholders or missing.

## Testing And Acceptance

### Deployment and configuration

- No tracked runtime example references `app.main:app`, Poetry, PostgreSQL, or ARQ.
- Every GitHub workflow uses frozen `uv.lock` installation and frozen commands.
- Production settings tests cover required environment values and positive health/mail recovery settings.

### Mail recovery

- A stale rendering task is moved to retrying and enqueues an outbox event atomically.
- A stale SMTP sending task becomes delivery unknown without enqueueing a retry.
- A stale preview sending task becomes retrying and enqueues an event.
- Freshly leased and terminal tasks are untouched.
- Concurrent recovery attempts cannot recover the same row twice.

### Assets

- Async upload, read, cleanup, mail attachment loading, PDF conversion, and ZIP compression invoke offloaded synchronous functions.
- Existing content-policy, authorization, size-limit, safe-name, and response-header tests remain green.
- Admin global asset access remains unchanged.

### Direct contracts

- The Web application exposes exactly one POST logout route.
- Logout still revokes the server-side refresh session and removes the cookie.
- Mail credential resolution rejects rows without encrypted credentials.
- The migration chain has exactly one head and drops the plaintext column.

### Health

- Liveness succeeds without contacting MySQL or Redis.
- Readiness returns 200 only when initialization, MySQL, and Redis are healthy.
- Either dependency failure or timeout returns 503.
- Database and Redis checks are started concurrently.

### Final quality gate

- Alembic reports one head and upgrades an allowlisted disposable database to head.
- Ruff and the hardened mypy target pass.
- The complete test suite passes against a migrated allowlisted MySQL database and Redis.

