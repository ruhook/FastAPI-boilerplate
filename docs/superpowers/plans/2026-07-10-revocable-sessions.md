# Revocable Sessions Implementation Plan

**Goal:** Replace long-lived identity JWT assumptions with short-lived, versioned access tokens and server-side, rotating refresh sessions for Web and Admin, while preserving the explicitly enabled local virtual admin.

**Architecture:** Access tokens remain JWTs and identify an immutable account id plus portal and token version. Normal refresh tokens become opaque random values whose SHA-256 hashes are stored in MySQL. Refresh rotation locks one session row, revokes it, creates its child in the same family, and treats reuse as family compromise. The virtual local admin keeps its no-database signed refresh path because it is available only under the explicit local bypass flag.

**Constraints:** Existing production JWTs are intentionally invalidated once strict claims are deployed. API paths and Admin response fields remain stable. Request dependencies own commits; session services only flush. Database-backed integration tests require explicit approval before destructive cleanup.

## Task 1: Schema and access-token contract

- Add `token_version` to `user` and `admin_user`.
- Add `auth_refresh_session` with token hash, portal/account id, family/parent, expiry, revocation, rotation, last-used, reason, and optional user-agent hash.
- Add Alembic revision `20260710_000041` and register the model.
- Require `sub`, `portal`, `token_type`, `ver`, `iat`, and `jti` in JWT verification.
- Set Web/Admin access-token defaults to 15 minutes.
- Test strict claims, token type, malformed ids, and token versions without a database.

## Task 2: Opaque refresh-session state machine

- Add token generation/hash helpers and session create, rotate, revoke-token, and revoke-account services.
- Use `SELECT ... FOR UPDATE` for rotation and revocation.
- Make rotation single-use; reuse of a rotated token revokes the active family.
- Never persist or log the raw refresh token.
- Add pure helper tests plus migration-head and lint checks.

## Task 3: Web authentication integration

- Issue access JWTs from user id/version and opaque refresh cookies.
- Rotate refresh cookies on every refresh.
- Add Web logout that revokes the supplied cookie and deletes it.
- Load current users by id and reject token-version mismatches.
- Password reset increments token version and revokes all refresh sessions without committing inside the endpoint.

## Task 4: Admin authentication integration

- Issue normal Admin access JWTs and opaque refresh tokens with the existing JSON response shape.
- Rotate and revoke Admin refresh sessions in `/refresh` and `/logout`.
- Preserve local virtual Admin login/refresh without database persistence, but require the local bypass flag for every access and refresh.
- Password changes, password-based admin updates, disable, and delete increment token version and revoke account sessions.

## Task 5: Verification and rollout docs

- Add focused unit/API-contract tests and database integration tests (run only against an explicitly approved disposable database).
- Verify compile, focused Ruff, Alembic single head, and non-database tests.
- Document one-time re-login, access-token lifetime, opaque refresh storage, and the local bypass exception.
