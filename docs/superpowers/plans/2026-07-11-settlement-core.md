# Settlement Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce persistent Payable and immutable Payment models with database-backed idempotent payout and reversal flows.

**Architecture:** Create focused `payable` and `payment` modules plus application payout orchestration. The old external payment-record routes are removed in this plan. The internal module and table remain only for the referral payout caller and are deleted in the referral-settlement task immediately after that caller migrates.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, MySQL, Pytest

## Global Constraints

- No legacy payment JSON migration or compatibility endpoints.
- `source_key` and payment uniqueness must be enforced by MySQL, not Python pre-checks.
- Payment rows are immutable; corrections create reversal rows.
- All production changes follow a witnessed failing test.

---

### Task 1: Payable and Payment persistence contracts

**Files:**
- Create: `src/app/modules/payable/const.py`
- Create: `src/app/modules/payable/model.py`
- Create: `src/app/modules/payment/const.py`
- Create: `src/app/modules/payment/model.py`
- Create: `src/migrations/versions/20260711_000048_settlement_core.py`
- Modify: `tests/conftest.py`
- Test: `tests/modules/test_settlement_models.py`

**Interfaces:**
- Produces: `Payable`, `PayableTimesheetSource`, `Payment`, `PayableStatus`, `PaymentEntryType`.
- Database guarantees: unique `payable.source_key`, unique `(payment.payable_id, payment.entry_type)`, unique `payment.reversal_of_payment_id`.

- [ ] **Step 1: Write the failing model contract tests**

```python
def test_payable_source_key_and_payment_entries_are_unique() -> None:
    assert Payable.__table__.c.source_key.unique is True
    names = {constraint.name for constraint in Payment.__table__.constraints}
    assert "uq_payment_payable_entry_type" in names
    assert "uq_payment_reversal_of" in names

def test_payable_uses_optimistic_versioning() -> None:
    column = Payable.__table__.c.version
    assert Payable.__mapper__.version_id_col is column
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/modules/test_settlement_models.py -q`

Expected: collection fails because `src.app.modules.payable` and `src.app.modules.payment` do not exist.

- [ ] **Step 3: Implement enums and models**

```python
class PayableStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PAID = "paid"
    CANCELLED = "cancelled"
    REVERSED = "reversed"

class PaymentEntryType(StrEnum):
    PAYMENT = "payment"
    REVERSAL = "reversal"
```

Define `Payable` with typed settlement fields and version mapping. Define `Payment` without update/delete mixins and with named unique constraints. Define `PayableTimesheetSource` with the composite unique source constraint from the design.

- [ ] **Step 4: Add the destructive development migration**

The migration creates `payable`, `payable_timesheet_source`, and `payment`. It does not alter `payment_record`, so the repository remains runnable while callers move.

- [ ] **Step 5: Update database cleanup and run GREEN**

Delete in foreign-key order: Payment, PayableTimesheetSource, Payable, then source records. Run:

`env ALLOW_TEST_DATABASE_CLEANUP=true uv run pytest tests/modules/test_settlement_models.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/app/modules/payable src/app/modules/payment src/migrations/versions/20260711_000048_settlement_core.py tests/conftest.py tests/modules/test_settlement_models.py
git commit -m "feat: add payable and immutable payment models"
```

### Task 2: Payable state policy and source keys

**Files:**
- Create: `src/app/modules/payable/policy.py`
- Create: `src/app/modules/payable/source_keys.py`
- Test: `tests/modules/test_payable_policy.py`

**Interfaces:**
- Produces: `ensure_payable_transition(current, target) -> None`.
- Produces: `salary_source_key`, `team_leader_bonus_source_key`, `referral_reward_source_key`, `manual_source_key`.

- [ ] **Step 1: Write failing transition and key tests**

```python
@pytest.mark.parametrize("current,target", [("pending", "processing"), ("processing", "paid"), ("paid", "reversed")])
def test_allowed_payable_transitions(current: str, target: str) -> None:
    ensure_payable_transition(PayableStatus(current), PayableStatus(target))

def test_salary_source_key_ignores_amount() -> None:
    assert salary_source_key(month="2026-07", user_id=3, contract_record_id=9) == "salary:2026-07:3:9"
```

- [ ] **Step 2: Run RED**

Run: `uv run pytest tests/modules/test_payable_policy.py -q`

Expected: imports fail because policy and key builders are absent.

- [ ] **Step 3: Implement the exact transition matrix and stable key builders**

Invalid transitions raise `ConflictException("Payable status changed or transition is not allowed.")`. `manual_source_key()` uses UUID7 text.

- [ ] **Step 4: Run GREEN and commit**

```bash
uv run pytest tests/modules/test_payable_policy.py -q
git add src/app/modules/payable tests/modules/test_payable_policy.py
git commit -m "feat: define payable state and identity rules"
```

### Task 3: Payable commands and database pagination

**Files:**
- Create: `src/app/modules/payable/schema.py`
- Create: `src/app/modules/payable/commands.py`
- Create: `src/app/modules/payable/queries.py`
- Test: `tests/modules/test_payable_commands.py`
- Test: `tests/admin/test_payables.py`

**Interfaces:**
- Produces: `upsert_pending_payable(db, draft) -> Payable`.
- Produces: `create_manual_payable(db, payload, admin_user_id) -> Payable` using a server-generated `manual:{uuid}` source key.
- Produces: `transition_payables(db, ids, target, admin_user_id) -> list[Payable]`.
- Produces: `list_payables(db, query) -> PayableListPage` with SQL pagination and summary.

- [ ] **Step 1: Write a failing idempotent upsert integration test**

```python
first = await upsert_pending_payable(db=session, draft=draft)
second = await upsert_pending_payable(db=session, draft=replace(draft, amount=Decimal("12.00")))
assert first.id == second.id
assert second.amount == Decimal("12.00")
```

- [ ] **Step 2: Run RED**

Run: `env ALLOW_TEST_DATABASE_CLEANUP=true uv run pytest tests/modules/test_payable_commands.py -q`

Expected: import failure for the new command.

- [ ] **Step 3: Implement MySQL-safe upsert and locked ordered transitions**

Use a nested transaction around insert, recover the row by unique `source_key` after `IntegrityError`, and only update existing rows in `pending`. Lock multiple IDs in ascending order before transitioning.

- [ ] **Step 4: Add SQL-backed list and summary API tests**

Assert filtering by month/type/status, deterministic ordering, page size, and summary values without in-memory full-history filtering.

- [ ] **Step 5: Run GREEN and commit**

```bash
env ALLOW_TEST_DATABASE_CLEANUP=true uv run pytest tests/modules/test_payable_commands.py tests/admin/test_payables.py -q
git add src/app/modules/payable tests/modules/test_payable_commands.py tests/admin/test_payables.py
git commit -m "feat: add persistent payable commands and queries"
```

### Task 4: Idempotent payout and reversal application use cases

**Files:**
- Create: `src/app/application/__init__.py`
- Create: `src/app/application/payouts.py`
- Create: `src/app/modules/payment/schema.py`
- Create: `src/app/modules/payment/commands.py`
- Create: `src/app/modules/payment/queries.py`
- Test: `tests/modules/test_payout_concurrency.py`
- Test: `tests/modules/test_payment_reversal.py`

**Interfaces:**
- Produces: `pay_payables(db, payable_ids, details, admin_user_id) -> BatchPayoutResult`.
- Produces: `reverse_payment(db, payment_id, details, admin_user_id) -> PaymentRead`.

- [ ] **Step 1: Write the concurrent duplicate-payment test**

Create one processing Payable, run two independent `local_session()` calls through `asyncio.gather`, then assert exactly one ordinary Payment exists and both results identify that Payment.

- [ ] **Step 2: Run RED**

Run: `env ALLOW_TEST_DATABASE_CLEANUP=true uv run pytest tests/modules/test_payout_concurrency.py -q`

Expected: payout use case is missing.

- [ ] **Step 3: Implement locked idempotent payment creation**

Lock Payable with `FOR UPDATE`. If paid, compare amount/platform/transaction number and return the existing Payment only when they match; otherwise raise 409. Insert Payment and update Payable in one savepoint.

- [ ] **Step 4: Write and run the failing reversal tests**

Test positive payment, negative reversal, one reversal per original, and Payable status `reversed`.

- [ ] **Step 5: Implement reversal and run GREEN**

```bash
env ALLOW_TEST_DATABASE_CLEANUP=true uv run pytest tests/modules/test_payout_concurrency.py tests/modules/test_payment_reversal.py -q
git add src/app/application src/app/modules/payment tests/modules/test_payout_concurrency.py tests/modules/test_payment_reversal.py
git commit -m "feat: make payout and reversal idempotent"
```

### Task 5: Replace external settlement APIs

**Files:**
- Create: `src/app/admin/api/v1/payables.py`
- Create: `src/app/admin/api/v1/payments.py`
- Modify: `src/app/admin/api/v1/__init__.py`
- Modify: `src/app/api/v1/me.py`
- Delete: `src/app/admin/api/v1/payment_records.py`
- Test: `tests/admin/test_payables.py`
- Test: `tests/admin/test_payments.py`
- Test: `tests/web/test_payments.py`

**Interfaces:** Implements the exact `/payables`, `/payments`, and `/me/payments` routes in the approved design and unregisters every external `/payment-records` and `/me/earnings` route.

- [ ] **Step 1: Write API tests for new routes and old-route removal**

```python
response = await admin_client.get("/v1/payables", headers=auth_headers)
assert response.status_code == 200
assert (await admin_client.get("/v1/payment-records", headers=auth_headers)).status_code == 404
assert (await web_client.get("/v1/me/payments", headers=user_headers)).status_code == 200
```

Also post one manual payable to `/v1/payables/manual` and assert it is returned as `pending` without creating a Payment.

- [ ] **Step 2: Run RED**

Run: `env ALLOW_TEST_DATABASE_CLEANUP=true uv run pytest tests/admin/test_payables.py tests/admin/test_payments.py tests/web/test_payments.py -q`

Expected: new routes return 404 and old route still exists.

- [ ] **Step 3: Implement thin route modules and update router registration**

Routes validate Pydantic DTOs, apply existing `流水记录` permission, call query/application functions, and return typed responses. `/v1/payables/manual` calls `create_manual_payable`; it never inserts Payment directly.

- [ ] **Step 4: Update imports, fixtures, and talent payment aggregation; delete old module**

Replace API, candidate-payment, and talent-detail imports with `payable` or `payment`. The referral-only internal import is removed in the referral-settlement plan; it is not registered as a compatibility route.

- [ ] **Step 5: Run plan verification and commit**

```bash
env ALLOW_TEST_DATABASE_CLEANUP=true uv run pytest tests/modules/test_settlement_models.py tests/modules/test_payable_policy.py tests/modules/test_payable_commands.py tests/modules/test_payout_concurrency.py tests/modules/test_payment_reversal.py tests/admin/test_payables.py tests/admin/test_payments.py tests/web/test_payments.py -q
uv run ruff check src/app/application src/app/modules/payable src/app/modules/payment tests/modules/test_*payment* tests/admin/test_payables.py tests/admin/test_payments.py
cd src && uv run alembic heads
git add src tests
git commit -m "refactor: replace payment records with settlement APIs"
```

Expected: tests pass and Alembic prints `20260711_000048 (head)` once.
