# Business Layering Overhaul Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the mixed payment/business-service design with explicit settlement, contract ownership, focused services, and synchronized server/admin/candidate contracts.

**Architecture:** Keep a modular monolith. Execute five dependent vertical plans in order; each leaves all three products in a testable state and is committed independently.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, MySQL, Pytest, React, TypeScript, Vitest, Vite

## Global Constraints

- Do not preserve legacy fields, statuses, routes, JSON fallback reads, or dual writes.
- Keep request transaction ownership in `async_get_db`; application/domain functions may flush but must not commit or globally roll back.
- Synchronize API changes across `hr-server`, `admin-web-next`, and `candidate-web-next`.
- Preserve the intentional attachment-access policy that exposes admin-owned attachment data to authenticated accounts.
- Use test-first RED/GREEN cycles for every behavior change.
- Use isolated worktrees so existing windows and uncommitted files are untouched.

---

## Execution Order

1. [Settlement core](2026-07-11-settlement-core.md)
2. [Timesheet and referral settlement integration](2026-07-11-timesheet-referral-settlement.md)
3. [Contract state ownership](2026-07-11-contract-state-ownership.md)
4. [Business service decomposition](2026-07-11-business-service-decomposition.md)
5. [Three-client contract synchronization and final verification](2026-07-11-three-client-contract-sync.md)

Each plan is a reviewer-sized gate. Do not begin the next plan until the current plan's focused tests and stated verification commands pass and its changes are committed.

## Worktree Layout

- Server: `/Users/ruanhaokang/workspace/hr/.worktrees/hr-server-business-layering`
- Admin: `/Users/ruanhaokang/workspace/hr/.worktrees/admin-business-layering`
- Candidate: `/Users/ruanhaokang/workspace/hr/.worktrees/candidate-business-layering`

Use branch `refactor/business-layering-overhaul` in each repository. The server worktree already exists at design commit `e4c19ac`. Before creating frontend worktrees, record each source branch SHA and create the branch from that exact SHA; do not switch or clean the existing frontend directories.

## Program Verification

Run after all five plans:

```bash
cd /Users/ruanhaokang/workspace/hr/.worktrees/hr-server-business-layering
env ALLOW_TEST_DATABASE_CLEANUP=true uv run pytest -q
uv run ruff check src tests
uv run mypy src/app/application src/app/modules/payable src/app/modules/payment src/app/modules/project_timesheet_record src/app/modules/referral src/app/modules/contract_record src/app/modules/job_progress src/app/modules/talent_profile src/app/modules/job
cd src && uv run alembic heads && uv run alembic upgrade head

cd /Users/ruanhaokang/workspace/hr/.worktrees/admin-business-layering
npm test
npm run build

cd /Users/ruanhaokang/workspace/hr/.worktrees/candidate-business-layering
npm test
npm run build
```

Expected: every command exits 0, Alembic prints exactly one head, and pytest reports no unexpected warnings.

