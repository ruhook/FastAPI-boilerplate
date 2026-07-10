# Server Foundation Hardening Design

## Goal

Harden the existing FastAPI modular monolith so that production configuration fails closed, credentials and sessions have explicit lifecycles, database-backed workflows do not lose asynchronous work, concurrent recruitment updates cannot silently overwrite one another, protected assets remain protected, and repository quality gates reflect the code that is actually deployable.

The existing `web` and `admin` application entrypoints, MySQL database, Redis deployment, Alembic migration system, and module-oriented repository structure remain in place. This is an in-place hardening effort, not a microservice rewrite.

## Scope And Constraints

- Work on the current branch; do not create a new branch or worktree.
- Existing users and administrators may be required to sign in again once session versioning is deployed.
- Preserve passwordless local-development administrator access behind an explicit local-only feature flag.
- Existing SMTP credentials may be migrated with an encryption key or re-entered when automatic migration is not possible.
- Preserve current public API paths unless a path is intrinsically unsafe. Response schemas may stop returning secret values.
- Preserve the `web`/`admin` split and shared database.
- Use test-first implementation for every behavior change.
- Migrations must be forward-only and safe for an existing MySQL deployment.
- No new infrastructure service is required beyond the existing MySQL, Redis, application workers, and supervisor-managed event consumer.

## Chosen Approach

Use layered in-place hardening:

1. Establish fail-closed configuration, secret handling, and log redaction.
2. Add server-side refresh sessions and account token versions while retaining signed JWT access tokens.
3. Add a database transactional outbox and make Redis Streams a transport rather than the source of truth.
4. Centralize recruitment mutations behind locked transition helpers and explicit transaction ownership.
5. Make asset delivery protected by default and add bounded, compensating storage operations.
6. Repair repository quality gates and run migrations, targeted tests, and the complete safe regression suite.

This approach is preferred over a minimal patch because a minimal patch would leave event loss and concurrent state corruption unresolved. It is preferred over replacing authentication and queues with external platforms because that would create substantially more migration and operational risk than the current product needs.

### Delivery subprojects

The umbrella design is delivered through separate, sequential implementation plans. Each subproject must be tested and deployable before the next one begins:

1. **Security baseline:** fail-closed configuration, explicit local authentication/bootstrap flags, log redaction, secret removal, encrypted/write-only SMTP credentials, and candidate presentation safe fallback.
2. **Revocable sessions:** immutable token subjects, account token versions, refresh-session rotation, logout, password-change/reset revocation, and account-disable revocation.
3. **Reliable events:** transactional outbox, dispatcher leases, Redis pending recovery, idempotent handlers, and mail delivery state hardening.
4. **Transactional recruitment:** request-owned transactions, deterministic row locking, optimistic versions, transition command boundaries, and concurrency tests.
5. **Protected assets:** private delivery, bounded streaming, storage compensation, physical deletion, and authorization regression tests.
6. **Permissions, performance, and gates:** strict RBAC migration, SQL pagination/summary queries, incremental service decomposition, CI services, lint, and type-check gates.

Cross-subproject interfaces defined here—token versions, outbox event ids, recruitment versions, and protected asset URLs—must not be renamed by later plans without updating this design and the already-shipped compatibility tests.

## 1. Production Configuration And Secret Safety

### Fail-closed environment validation

`Settings` will validate production invariants at startup. Production startup must fail when any of the following is true:

- `SECRET_KEY` is a known placeholder or too short.
- wildcard CORS is enabled while credentials are allowed.
- local automatic administrator login or bootstrap is enabled in a non-local environment.
- candidate verification is enabled without complete SMTP settings.
- Aliyun OSS is selected without complete credentials and bucket configuration.
- the SMTP credential encryption key is missing.

Local passwordless administrator access is preserved, but it requires both `ENVIRONMENT=local` and `ENABLE_LOCAL_AUTH_BYPASS=true`. `ENVIRONMENT=local` alone no longer activates the bypass. The setting default is `false`; the local `.env.example` may enable it explicitly so the existing development workflow remains one-step. Production validation rejects the bypass regardless of any other setting.

Database-backed local administrator creation is controlled independently by `ENABLE_LOCAL_ADMIN_BOOTSTRAP`. This prevents the passwordless virtual identity and persistent seed account from being coupled accidentally.

### Request log redaction

Request logging will parse JSON and form bodies into key/value structures, recursively replace sensitive values, and only then serialize the bounded log payload. The redaction catalog includes at least:

- `password`, `current_password`, `new_password`, `confirm_password`, `confirm_new_password`
- `access_token`, `refresh_token`, `token`, `authorization`
- `secret`, `secret_key`, `auth_secret`, `access_key_secret`
- verification codes and SMTP authorization values

Multipart bodies remain omitted. Malformed bodies are represented by a fixed placeholder instead of being logged raw. Query parameters with sensitive names are redacted by the same helper.

### SMTP credential encryption

Mail account SMTP authorization values will be encrypted before persistence with an application-level key supplied through `MAIL_CREDENTIAL_ENCRYPTION_KEY`. The API accepts an `auth_secret` on create/update but never returns it. Read responses expose `has_auth_secret: bool` instead.

Encryption uses authenticated encryption. Ciphertext includes a version prefix so future key rotation can support multiple decryptors. A migration adds a new encrypted column without attempting to reinterpret unknown plaintext automatically. A one-off migration command encrypts existing values when the operator supplies the key; otherwise affected accounts remain disabled until their credential is re-entered. After verified migration, the plaintext column is cleared.

The credential-like default currently present in source configuration is removed. Operational rollout must rotate that external credential because removing it from the current revision does not remove it from Git history.

## 2. Revocable Authentication Sessions

### Access tokens

Access tokens remain signed JWTs but become short-lived:

- Web access token: 15 minutes by default.
- Admin access token: 15 minutes by default.
- Web refresh session: 15 days by default.
- Admin refresh session: 30 days by default.

JWT payloads include immutable account id (`sub`), portal, token type, account token version, issued-at time, and a unique token id. Username and email are display/login identifiers, not token subjects.

Every authenticated request loads the account and verifies that the JWT token version matches the account's current token version. Account disable/delete, password change, and password reset increment the token version, immediately invalidating existing access tokens.

### Refresh sessions

Refresh tokens are opaque random values. Only a cryptographic hash is stored in a new `auth_refresh_session` table together with:

- portal and account id
- expiration and revocation timestamps
- rotation family id and parent session id
- created and last-used timestamps
- optional client metadata such as user-agent hash

Refresh is single-use rotation: a valid session is revoked and replaced atomically. Reuse of a rotated token revokes the entire family. Logout revokes the supplied refresh session. Password changes, password resets, account disable/delete, and explicit “logout all” revoke all sessions for that account.

Web refresh tokens remain in `HttpOnly`, `Secure` cookies outside local development. Admin refresh tokens may remain in the JSON client contract during the compatibility phase, but still use the same server-side session table and rotation rules.

### Local admin behavior

Local bootstrap creates a real database administrator only when the explicit bootstrap flag is enabled. The virtual development identity remains available when the separate local-auth bypass flag is enabled. Tokens for that identity are accepted only while the process is both local and explicitly bypass-enabled; changing either condition invalidates further authentication and refresh.

## 3. Transactional Events And Mail Delivery

### Database outbox

Business operations that require an asynchronous action insert an `event_outbox` row in the same database transaction as the business data. An outbox row contains:

- stable event id and event type
- JSON payload
- status, available-at time, attempt count, and maximum attempts
- lease owner and lease expiration
- published, processed, and failed timestamps
- last error summary

No request publishes directly to Redis before its database transaction commits.

### Dispatcher

The event consumer process gains an outbox dispatcher loop. It claims due rows using database row locking, publishes their stable event ids to Redis Streams, and marks publication state. If the process crashes between publish and marking, the event may be published twice; consumers therefore use the event id for idempotency.

Redis delivery failure leaves the outbox row due for retry with bounded exponential backoff. Exhausted events move to a failed state that is queryable and manually retryable; they are not silently discarded.

### Redis consumer recovery

Redis messages are acknowledged only after all registered handlers succeed. Handler exceptions propagate to the queue layer. The consumer periodically claims stale pending messages using `XAUTOCLAIM`, and records delivery attempts. Unknown event types are moved to the failed/dead-letter record instead of acknowledged without evidence.

### Mail idempotency

Mail processing claims a `MailTask` under a row lock. A task already marked `sent` is a no-op. A stable delivery idempotency key is recorded before SMTP send. Since SMTP itself does not provide a universal exactly-once contract, the system guarantees durable retry and prevents concurrent duplicate workers; ambiguous failures are marked separately for operator review rather than automatically resent without a decision.

Mail task status transitions are explicit and validated: `pending -> rendering -> sending -> sent`, with `failed`, `retrying`, and `delivery_unknown` branches.

## 4. Transactions, Concurrency, And Recruitment State

### Transaction ownership

The request database dependency owns commit/rollback for normal API commands. Domain services flush but do not commit unless they are documented background-worker transaction boundaries. Existing service-level commits are removed from request paths.

Outbox records, operation logs, recruitment state, contract changes, and mail task creation therefore commit or roll back together.

### Locked recruitment commands

Every command that mutates a `JobProgress` row loads it with `SELECT ... FOR UPDATE` inside the owning transaction. Batch commands lock rows in ascending primary-key order to reduce deadlocks. Contract rows updated by the same command are locked in deterministic order as well.

`JobProgress` gains a monotonically increasing version. API mutation requests may supply the version; when supplied, a stale version returns HTTP 409 instead of overwriting a newer update. Internal commands still use row locks even when the caller omits a version.

### State source of truth

The existing database stages remain in place, but mutation rules move behind a focused recruitment command boundary. Direct assignments to `current_stage` outside this boundary are eliminated over the course of the migration.

Stable workflow fields that participate in invariants remain explicit columns or contract records. The JSON `data` field remains for presentation metadata and backward-compatible extension values, but updates use named helpers and are included in the same locked command.

### Candidate presentation safety

Candidate presentation first validates the recruitment stage, then derives actions. Unknown or incomplete states always return `Under Review / Application Review / View Details`. Contract and assessment actions are only emitted when both the stage and required artifacts/invitations are consistent. An `assessment_review` row without submission evidence does not claim that an assessment was submitted.

## 5. Protected Asset Storage

### Access model

The canonical asset URL is an authenticated API preview/download URL. The API does not return a permanent public OSS object URL for resumes, contracts, identity documents, assessment files, or timesheet attachments.

When direct object delivery is necessary, the server issues a short-lived signed URL after the same authorization check used by the API endpoint. Bucket ACLs are expected to be private.

### Bounded upload and download

Uploads enforce size limits while reading in chunks and reject oversized content before buffering the whole file. Allowed extensions and MIME types are defined per asset purpose. Batch ZIP generation writes to a temporary spool and streams the response instead of building the entire archive in memory. Per-request file count and aggregate byte limits are enforced.

### Storage compensation and deletion

An asset upload records storage intent, writes the object, and creates the database record. If the database operation fails, compensation deletes the newly written object. A periodic reconciliation command reports orphaned database rows and orphaned objects.

Soft deletion schedules physical deletion after a retention window. Immediate deletion is available for failed uploads and exposed secrets. Physical deletion events are idempotent and auditable.

## 6. Permission Model

The current implicit rule that every non-reviewer administrator receives all default business permissions will be made explicit during migration and then replaced by actual role grants. Superusers retain all permissions. Ordinary administrators receive only the permissions stored on their enabled role; an account without a role receives no business permission.

The permission catalog contains all business, settings, and special permissions. `require_admin_permission` and `require_any_admin_permission` always evaluate the effective permission set instead of special-casing assessment reviewers.

This change is intentionally allowed to remove access from existing ordinary administrators. The migration creates an `Existing Full Access` role containing the current default permissions and assigns it to existing eligible accounts so rollout behavior remains controlled rather than accidental.

## 7. Performance And Module Boundaries

Candidate and administrator list endpoints perform count, filtering, ordering, and pagination in SQL. Full-result presentation summaries use aggregate SQL expressions or a bounded summary query; list endpoints do not materialize every matching ORM row before slicing.

The `job_progress/service.py` module is decomposed along command and query responsibilities while preserving public service interfaces during migration:

- `commands.py`: locked state-changing operations
- `queries.py`: paginated reads and projections
- `presentation.py`: candidate-facing state derivation and summaries
- `mail_workflow.py`: mail/outbox coordination
- `contracts.py`: recruitment/contract transition coordination

Decomposition follows behavior changes and tests; it is not performed as an unverified bulk move.

## 8. Error Handling And Observability

Security-sensitive failures return stable public messages and log redacted structured context. Logs include request id, account id when authenticated, event id, outbox id, mail task id, and retry attempt where relevant.

Readiness checks cover database and Redis. The event consumer exposes or logs lease health, pending counts, retry counts, oldest due outbox age, and failed-event counts. A failed Redis connection makes asynchronous readiness unhealthy but does not lose committed outbox events.

Conflict errors use HTTP 409. Authentication and revoked-session failures use HTTP 401. Permission failures use HTTP 403. Invalid workflow transitions use HTTP 409 rather than generic validation errors where the request was syntactically valid but stale or incompatible with current state.

## 9. Migration And Rollout Order

1. Deploy schema additions: token versions, refresh sessions, encrypted mail credential storage, recruitment version, and event outbox.
2. Deploy code that can read old mail credentials but writes only encrypted credentials; run the credential migration command or require re-entry.
3. Deploy revocable sessions. Existing JWTs without account id/version are rejected, causing the approved one-time re-login.
4. Enable outbox writes and dispatcher while retaining compatibility monitoring for old pending mail tasks.
5. Backfill pending mail tasks into the outbox with stable event ids.
6. Enable locked recruitment commands and version conflict responses.
7. Switch protected asset responses away from permanent public URLs and verify both frontends use authenticated/signed URLs.
8. Assign the migration full-access role to existing ordinary administrators, then enable strict RBAC.
9. Remove plaintext credential fallback after migration verification.
10. Enable the complete CI gate and deploy only when migrations, tests, lint, and type checking pass.

Each stage is independently reversible at the application level until destructive cleanup of plaintext secrets and public object access. Database migrations themselves remain forward-only.

## 10. Testing And Acceptance

### Unit tests

- Production settings reject unsafe defaults, local auth/bootstrap flags, and wildcard credentialed CORS.
- Local passwordless authentication succeeds only when its explicit bypass flag is enabled.
- Redaction covers nested JSON, form bodies, query parameters, malformed bodies, and all secret key aliases.
- Credential encryption round-trips, rejects tampering, and never serializes plaintext.
- JWT validation rejects stale account token versions.
- Refresh rotation rejects replay and revokes token families.
- Candidate presentation safely handles unknown and incomplete states.
- Permission dependencies enforce role grants for every ordinary administrator.

### Database integration tests

- Password changes, password resets, account disable/delete, logout, and logout-all revoke the appropriate sessions.
- Business mutation and outbox insertion commit and roll back atomically.
- Two concurrent recruitment commands cannot silently overwrite one another.
- Consumer failure leaves an event retryable; success acknowledges it; stale pending messages are reclaimed.
- Mail task workers do not concurrently deliver the same task.
- Asset persistence failures clean up newly written objects.

### API tests

- Mail account responses never contain `auth_secret`.
- Protected assets cannot be read by another candidate or an unauthorized administrator.
- Stale recruitment versions return 409.
- Existing Web/Admin paths continue to authenticate and return compatible non-secret fields.

### Repository gates

- Alembic reports one head and upgrades an empty database to head.
- `ruff check` passes without applying automatic fixes during verification.
- `mypy` passes for production application modules; scripts may be moved to a separately tracked gate only if the exclusion is explicit and documented.
- The database/Redis-backed pytest suite runs in CI against disposable services with destructive cleanup explicitly enabled only for the disposable database.
- Frontend builds and focused API-contract tests pass after session and asset response changes.

## Non-goals

- No microservice split.
- No replacement of FastAPI, SQLAlchemy, MySQL, Redis, Alembic, or supervisor.
- No introduction of Keycloak, Celery, Kafka, RabbitMQ, or a new secret-management service.
- No redesign of business recruitment stages beyond enforcing their existing invariants.
- No automatic recovery of an external credential already exposed in Git history; it must be rotated operationally.
