# Permissions, Performance, And CI Implementation Plan

## Goal

Remove implicit administrator access, eliminate the highest-impact unbounded/N+1 queries, and make repository checks accurately describe what CI can execute.

## Steps

1. Add pure regression tests proving ordinary administrators receive only explicit enabled-role grants and every permission dependency checks the effective set.
2. Expand the role catalog to business, settings, and special permissions; remove reviewer-specific authorization bypasses.
3. Add an Alembic migration that creates an `Existing Full Access` role, backfills current ordinary accounts without roles, and makes existing non-reviewer role grants explicit before strict RBAC is enabled.
4. Replace administrator-account role N+1 lookups with one outer-joined query and preserve disabled/missing-role behavior without writes during reads.
5. Add source/query regression tests for SQL pagination on selected high-volume list paths that can preserve their current response summaries.
6. Apply repository formatting/import fixes mechanically, then address or explicitly scope remaining lint/type debt without presenting a failing command as a working gate.
7. Add disposable MySQL and Redis services to the pytest workflow, explicitly enable destructive cleanup only for that disposable database, run Alembic first, and keep local cleanup disabled by default.
8. Run pure unit tests, focused query tests, Ruff, mypy, Alembic head checks, and non-destructive verification available in the current workspace.

## Safety

- Do not run the integration suite against the developer's local `hr_server` database or asset directory.
- Keep the explicit local `HaokangImport` virtual superuser bypass unchanged.
- Preserve existing ordinary-account access through migration data, not through an implicit runtime fallback.
