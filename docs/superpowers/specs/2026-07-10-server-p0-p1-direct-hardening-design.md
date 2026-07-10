# Server P0/P1 Direct Hardening Design

## Status

Approved in conversation on 2026-07-10. This design replaces compatibility-oriented choices for the five P0/P1 areas below because the product is still in development.

## Goal

Remove the remaining high-risk server foundation flaws without adding compatibility shims: prevent active-content attachment execution, make candidate application creation concurrency-safe, replace bcrypt with Argon2id, add authentication and verification abuse controls, and prevent poison events from starving Redis Stream consumption.

The existing FastAPI modular monolith, Web/Admin split, MySQL database, Redis deployment, transactional outbox, local authentication bypass, and supervisor-managed event consumer remain in place.

## Global Constraints

- Backward compatibility is not required for existing password hashes, unsafe asset metadata, old Redis pending messages, or obsolete configuration defaults.
- Database and Redis state may be reset in development instead of adding data-conversion fallbacks.
- Migrations must be deterministic for a clean database. They must fail visibly on conflicting existing data rather than silently selecting or deleting user records.
- All authenticated administrator accounts may access Admin business data and Admin attachment APIs. This is an intentional product rule and must be protected by regression tests.
- The local passwordless administrator flow remains available only through the existing explicit local-only feature flag.
- No new infrastructure service is introduced. MySQL and Redis remain the only required stateful services.
- Every behavior change is implemented test-first.

## Chosen Approach

Use a direct replacement rather than a compatibility layer:

1. Classify assets from their bytes and extension, and use a server-owned delivery policy.
2. Enforce active-application uniqueness in MySQL and update counters atomically.
3. Replace bcrypt with Argon2id everywhere.
4. Centralize Redis-backed authentication abuse controls and public response normalization.
5. Replace the serial Redis Stream loop with a bounded worker pool, delivery limits, and a dead-letter stream.

This is preferred over small local guards because local guards would leave inconsistent behavior across Web and Admin entrypoints. It is preferred over separate preview, antivirus, identity, or queue services because those services are not required to close the current risks.

## 1. Safe Asset Classification And Delivery

### Classification

The upload pipeline will not persist `UploadFile.content_type` as authoritative metadata. A focused asset-classification module will receive the original filename and bounded file bytes and return a server-owned media type and delivery disposition.

Classification requires agreement between the extension and a recognized file signature. The initial allowlist covers the formats currently needed by resumes, contracts, assessments, mail attachments, signatures, and rich-text images:

- raster images: JPEG, PNG, GIF, and WebP;
- documents: PDF, DOCX, XLSX, and PPTX;
- legacy Office compound documents: DOC, XLS, and PPT;
- UTF-8 plain text;
- ZIP archives where the calling asset purpose permits archives.

HTML, XHTML, SVG, XML, JavaScript, and files whose extension/signature do not agree are rejected. Unknown binary content is rejected instead of being stored as client-declared `application/octet-stream`.

Programmatically generated assets pass through the same classifier. Their supplied MIME value is an assertion checked against the detected result, not an override.

### Delivery policy

Only recognized raster images may be delivered inline. Every other supported format uses `Content-Disposition: attachment`. Preview and download responses include `X-Content-Type-Options: nosniff`; inline responses also include a restrictive Content Security Policy and a server-generated safe filename.

Authenticated access rules are:

- candidate assets still require the candidate ownership/reference checks already present;
- any authenticated Admin account may access every Admin-visible asset, including mail assets, regardless of business role grants or which Admin account created it.

Historical rows with untrusted MIME metadata receive no fallback. A development database reset or asset re-upload is the supported migration path.

## 2. Concurrency-Safe Candidate Applications

### Database invariant

`candidate_application` gains a generated nullable column representing the active job id: it contains `job_id` while `is_deleted = false` and `NULL` after soft deletion. A unique index on `(user_id, active_job_id)` enforces at most one active application per candidate and job while allowing deleted history.

The migration does not deduplicate conflicting rows. If an existing development database violates the invariant, the operator resets or repairs that database before upgrading.

### Command behavior

Application creation may retain an early duplicate check for a friendly response, but correctness relies on the unique index. A matching `IntegrityError` is translated to the existing duplicate-application HTTP error; unrelated integrity failures propagate.

`Job.applicant_count` is incremented with a single SQL expression (`applicant_count = applicant_count + 1`) in the same transaction as the new application. The in-memory read-modify-write sequence is removed. Existing request-owned transaction and outbox behavior remain unchanged.

Concurrency tests use separate database sessions and prove that two simultaneous submissions produce one application, one conflict, and a count increment of exactly one.

## 3. Argon2id Password Hashing

The server adds the maintained Argon2 password-hashing library and removes direct bcrypt use and the bcrypt dependency. `get_password_hash` always creates an Argon2id hash with one centrally configured `PasswordHasher`; `verify_password` accepts only a valid Argon2 encoded hash.

No bcrypt detection, fallback verification, or login-time rehash is implemented. Existing Web and Admin accounts with bcrypt hashes must be recreated or have their passwords reset in development.

All password creation, registration, reset, and change paths continue to call the shared security helpers. Existing character-length API validation remains, and a central maximum UTF-8 byte length prevents unexpectedly large hashing input without reintroducing bcrypt's 72-byte truncation behavior.

Authentication for a missing, disabled, or unknown account performs one verification against a process-initialized dummy Argon2 hash before returning failure. Public authentication responses remain identical for unknown accounts, wrong passwords, and disabled accounts.

## 4. Authentication And Verification Abuse Controls

### Central limiter

A focused authentication rate-limit module uses Redis and an atomic Lua operation to increment a key and set its expiry together. Identifiers stored in keys are normalized and HMAC-derived with `SECRET_KEY` plus a domain-separation label; raw email addresses and usernames are not placed in Redis keys.

The initial limits are configuration-backed and fail closed outside the local environment:

- login: per IP, per normalized identifier, and per IP/identifier pair;
- verification-code send: per IP and per normalized email, in addition to the existing resend cooldown;
- verification-code check: per IP and per normalized email, in addition to the code's remaining-attempt counter.

Rate-limit responses use HTTP 429 and a `Retry-After` header. Local passwordless Admin authentication is not rate-limited because it performs no credential verification; ordinary local password login still uses the limiter.

### Request identity

API routes pass the trusted client address from the request object into the limiter. Proxy headers are trusted only when the deployment's existing proxy configuration explicitly enables that behavior; arbitrary client-supplied forwarding headers are not accepted by application code.

### Enumeration and secret separation

Registration-code and password-reset-code requests return the same accepted response whether the account exists or not. In cases where no email should be sent, the service performs the limiter and bounded dummy work but does not create a usable verification code. SMTP failures are logged with redacted structured context and are not returned verbatim.

Verification-code hashes use HMAC-SHA256 with `SECRET_KEY` and a `verification-code` domain label. They no longer depend on the SMTP authentication secret. Rate-limit keys use a different domain label.

Local-only debug verification codes remain available under the existing local environment behavior. Production responses never expose a code or SMTP exception.

## 5. Bounded Event Workers And Dead-Letter Stream

### Worker model

The consumer starts one fetch loop and a bounded set of handler worker tasks equal to `EVENT_CONSUMER_CONCURRENCY`. The fetch loop places claimed messages into a bounded in-process queue; workers await handlers and acknowledge only after successful completion.

The fetcher alternates between stale pending recovery and new `>` messages so an old message cannot permanently starve new delivery. Shutdown stops fetching, drains in-flight work up to the configured shutdown timeout, and then cancels remaining tasks without acknowledging unfinished events.

### Delivery limit and DLQ

Before redispatching a pending item, the consumer reads its Redis delivery count. Once `EVENT_CONSUMER_MAX_DELIVERIES` is reached, it writes a dead-letter entry and acknowledges the original message. Dead-letter entries contain:

- original stream and message id;
- stable event id and event type when decodable;
- original payload or a bounded raw representation;
- delivery count, failure category, and redacted error summary;
- first-seen and dead-letter timestamps.

Malformed JSON, unknown event types, and handler failures all follow the same bounded-delivery policy. Moving to the dead-letter stream and acknowledging the source are performed through one Lua script so a crash cannot acknowledge without recording the dead letter.

The dead-letter stream has a configurable maximum length. This phase provides observable logs and Redis state; an operator-facing replay API is outside the P0/P1 scope.

Outbox rows remain the durable source for business events. Successful consumer handling records the existing completion state where supported. Old development streams and consumer groups are explicitly cleared during rollout rather than migrated.

## Error Handling And Observability

- Asset rejection returns a stable 400 response that names the unsupported format without echoing raw content.
- Duplicate applications return the existing conflict semantics and never expose database constraint names.
- Authentication failure text is stable across account existence and status.
- Rate-limit logs include only HMAC-derived identity keys, request id, portal, dimension, and retry-after value.
- Event logs include stream, message id, stable event id, delivery count, worker id, and dead-letter outcome, with bounded/redacted exceptions.
- Configuration validation rejects non-positive worker counts, delivery limits, queue sizes, and rate-limit windows.

## Migration And Development Reset

1. Add the generated active-job column and unique index to `candidate_application`.
2. Add Argon2 and remove bcrypt; recreate or reset development accounts.
3. Deploy asset classification; re-upload or discard existing assets with invalid metadata.
4. Deploy rate-limit configuration and clear old verification-code Redis keys.
5. Deploy the new event consumer and clear old development event streams/groups before starting it.

There is no dual-read, dual-write, bcrypt fallback, MIME fallback, pending-message converter, deprecated setting alias, or old-response compatibility layer.

## Testing And Acceptance

### Asset tests

- A fake HTML or SVG file cannot be made safe by declaring `image/png`.
- Extension/signature mismatches are rejected.
- Raster images use server-owned MIME and inline security headers.
- PDF and Office files are delivered as attachments with `nosniff`.
- A normal authenticated Admin with no business role can still read Admin-visible assets, including assets created by another Admin account.

### Application tests

- The migration has one Alembic head and upgrades an empty MySQL database.
- Concurrent duplicate submissions produce one active row and one conflict.
- Concurrent submissions by different users do not lose `applicant_count` increments.
- Soft-deleted history does not prevent a new active application.

### Password and abuse tests

- Newly generated hashes are Argon2id and arbitrary changes after byte 72 affect verification.
- A bcrypt hash is rejected.
- Web and Admin unknown-account paths execute dummy verification.
- Login and verification dimensions limit independently and return `Retry-After`.
- Registration/reset send responses do not reveal account existence.
- Production responses do not expose SMTP exceptions or verification codes.

### Event tests

- Configured concurrency produces multiple simultaneously active handlers without double-processing one message.
- A retryable failure stays pending below the limit.
- A poison or malformed message enters the dead-letter stream at the limit and is acknowledged from the source.
- New messages continue while stale failing messages exist.
- Graceful shutdown does not acknowledge unfinished work.

### Repository gates

- Focused tests demonstrate a red-green cycle for each behavior change.
- The complete safe test suite passes.
- `ruff check --no-fix src tests` passes.
- The existing core mypy gate passes and includes any new focused foundation modules.
- Alembic reports exactly one head.

## Non-goals

- No business-permission restriction for Admin data or Admin assets.
- No compatibility with existing bcrypt hashes, unsafe asset metadata, obsolete settings, or old Redis pending events.
- No antivirus or content-disarm service.
- No separate asset preview domain in this phase.
- No event DLQ management UI or automatic replay.
- No external identity provider or message broker.
- No audit-log expansion, mail payload-capacity work, CI dependency locking, deployment-document repair, or other P2 work in this phase.
