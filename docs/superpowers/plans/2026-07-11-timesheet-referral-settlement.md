# Timesheet and Referral Settlement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Materialize salary, team-leader, and referral Payables from typed source data while enforcing source audit, versioning, and freeze rules.

**Architecture:** Extract pure calculators from the old payment service, then let `application/settlement.py` coordinate source modules and Payable commands. Source modules never write payout status.

**Tech Stack:** SQLAlchemy async, MySQL, Pytest

## Global Constraints

- GET requests never create or mutate Payables.
- Pending Payables may recalculate; processing/paid/reversed Payables freeze their source rows.
- Referral payout summaries are derived from Payable/Payment.
- No old ReferralRecord payout columns or JSON fallback.

---

### Task 1: Pure settlement calculators

**Files:**
- Create: `src/app/modules/payable/calculator.py`
- Test: `tests/modules/test_payable_calculator.py`

**Interfaces:** Produces `calculate_salary`, `calculate_team_leader_bonus`, and `calculate_referral_milestones`, returning immutable `PayableDraft` values.

- [ ] Write failing tests by moving the amount examples from `tests/modules/test_payment_record_payables.py`, including rounding, base-pay split, zero-hour behavior, and referral caps.
- [ ] Run `uv run pytest tests/modules/test_payable_calculator.py -q`; expect import failure.
- [ ] Implement Decimal-only pure functions with `ROUND_HALF_UP` at currency boundaries.
- [ ] Run the test file; expect PASS.
- [ ] Commit with `git commit -m "refactor: extract settlement calculators"`.

### Task 2: Timesheet optimistic locking and idempotent batch requests

**Files:**
- Modify: `src/app/modules/project_timesheet_record/model.py`
- Modify: `src/app/modules/project_timesheet_record/schema.py`
- Create: `src/app/modules/project_timesheet_record/idempotency.py`
- Create: `src/migrations/versions/20260711_000049_timesheet_settlement_guards.py`
- Test: `tests/modules/test_project_timesheet_concurrency.py`
- Test: `tests/admin/test_timesheet_idempotency.py`

**Interfaces:** Adds `version`; batch create requires `idempotency_key`; stale updates raise 409.

- [ ] Write a failing model test asserting `ProjectTimesheetRecord.__mapper__.version_id_col`.
- [ ] Write a failing two-session update test where the second commit raises the translated 409.
- [ ] Write a failing API test posting the same idempotency key twice and asserting the same created IDs.
- [ ] Add the version column, request-idempotency table/helper, DTO field, and conflict translation.
- [ ] Run both test files and commit `feat: guard timesheet writes with version and idempotency`.

### Task 3: Source association and freeze policy

**Files:**
- Create: `src/app/modules/payable/source_policy.py`
- Modify: `src/app/modules/project_timesheet_record/commands.py`
- Test: `tests/modules/test_timesheet_settlement_freeze.py`

**Interfaces:** Produces `ensure_timesheets_editable(db, record_ids) -> None` and maintains `PayableTimesheetSource` snapshots.

- [ ] Write failing tests proving pending sources can change and processing/paid/reversed sources return 409 for update and delete.
- [ ] Implement one SQL query joining PayableTimesheetSource and Payable status; do not loop per record.
- [ ] Run `env ALLOW_TEST_DATABASE_CLEANUP=true uv run pytest tests/modules/test_timesheet_settlement_freeze.py -q`; expect PASS.
- [ ] Commit `feat: freeze settled timesheet sources`.

### Task 4: Settlement application orchestration

**Files:**
- Create: `src/app/application/settlement.py`
- Modify: `src/app/modules/project_timesheet_record/commands.py`
- Modify: `src/app/modules/contract_record/commands.py`
- Test: `tests/modules/test_settlement_sync.py`
- Test: `tests/modules/test_settlement_sync_concurrency.py`

**Interfaces:** Produces `sync_settlement_month`, `sync_timesheet_change`, and `sync_contract_rate_change`.

- [ ] Write failing integration tests for create/update/delete source changes updating one pending Payable.
- [ ] Write a failing two-session sync test asserting one source_key and one Payable.
- [ ] Implement affected-dimension calculation, source association replacement for pending rows, and unique-key recovery.
- [ ] Add `POST /v1/payables/sync` API coverage; ensure GET `/v1/payables` does not change row counts.
- [ ] Run focused tests and commit `feat: materialize payables from timesheet changes`.

### Task 5: Referral-derived settlement

**Files:**
- Modify: `src/app/modules/referral/model.py`
- Modify: `src/app/modules/referral/schema.py`
- Modify: `src/app/modules/referral/queries.py`
- Modify: `src/app/application/settlement.py`
- Create: `src/migrations/versions/20260711_000050_referral_settlement_ownership.py`
- Delete: `src/app/modules/payment_record/`
- Delete: `src/app/admin/api/v1/payment_records.py`
- Modify: `src/migrations/env.py`
- Modify: `tests/conftest.py`
- Test: `tests/modules/test_referral_settlement.py`
- Test: `tests/admin/test_referrals.py`
- Test: `tests/web/test_referrals.py`

**Interfaces:** Referral reward status and paid totals are query projections from Payable/Payment.

- [ ] Write failing tests for one Payable per crossed milestone, cap enforcement, paid aggregation, and reversed aggregation.
- [ ] Remove referral payout columns and the old `payment_record` table in the migration; delete the old internal module after migrating its final referral caller; add SQL aggregate queries.
- [ ] Trigger milestone sync after relevant timesheet changes.
- [ ] Run referral and settlement tests; expect PASS.
- [ ] Commit `refactor: derive referral payouts from settlement ledger`.

### Task 6: Verification

- [ ] Run:

```bash
env ALLOW_TEST_DATABASE_CLEANUP=true uv run pytest tests/modules/test_payable_calculator.py tests/modules/test_project_timesheet_concurrency.py tests/modules/test_timesheet_settlement_freeze.py tests/modules/test_settlement_sync.py tests/modules/test_settlement_sync_concurrency.py tests/modules/test_referral_settlement.py tests/admin/test_timesheet_idempotency.py tests/admin/test_referrals.py tests/web/test_referrals.py -q
uv run ruff check src/app/application/settlement.py src/app/modules/payable src/app/modules/project_timesheet_record src/app/modules/referral tests
```

Expected: all selected tests pass with no SQLAlchemy warnings.
