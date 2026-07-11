# Contract State Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ContractRecord the only contract-state authority and remove JSON/JobProgress compatibility state.

**Architecture:** Typed contract columns and policy functions own state. `application/contracting.py` coordinates contract changes with recruitment-stage transitions.

**Tech Stack:** FastAPI, SQLAlchemy async, Alembic, MySQL, Pytest

## Global Constraints

- Persist only snake-case enum values from the approved design.
- Delete legacy titled statuses and Chinese state values from storage logic.
- JobProgress may present contract state but may not store or mutate copies.

---

### Task 1: Typed contract state columns

**Files:**
- Modify: `src/app/modules/contract_record/const.py`
- Modify: `src/app/modules/contract_record/model.py`
- Modify: `src/app/modules/contract_record/schema.py`
- Create: `src/migrations/versions/20260711_000051_contract_state_columns.py`
- Test: `tests/modules/test_contract_state_model.py`

- [ ] Write failing tests asserting defaults `pending_activation`, `pending`, and `not_sent`, plus absence of JSON-based serialization.
- [ ] Add StrEnums, columns, indexes, and destructive migration defaults.
- [ ] Run `uv run pytest tests/modules/test_contract_state_model.py -q`; expect PASS.
- [ ] Commit `feat: type contract workflow state`.

### Task 2: Contract policy and application workflow

**Files:**
- Create: `src/app/modules/contract_record/policy.py`
- Create: `src/app/modules/contract_record/commands.py`
- Create: `src/app/application/contracting.py`
- Test: `tests/modules/test_contract_policy.py`
- Test: `tests/modules/test_contracting_workflow.py`

**Interfaces:** Produces explicit commands for draft upload, candidate signature, review, company seal, activation, termination, and expiry.

- [ ] Write failing transition-table tests, including rejected attempts to activate before approval and company seal.
- [ ] Write failing application tests proving activation advances JobProgress exactly once.
- [ ] Implement pure policy checks, locked ContractRecord commands, then application coordination.
- [ ] Run focused tests and commit `feat: centralize contract workflow ownership`.

### Task 3: Remove JobProgress contract copies and legacy fallbacks

**Files:**
- Modify: `src/app/modules/job_progress/const.py`
- Modify: `src/app/modules/job_progress/schema.py`
- Modify: `src/app/modules/job_progress/serialization.py`
- Modify: `src/app/modules/job_progress/filtering.py`
- Modify: `src/app/modules/job_progress/commands.py`
- Delete: `src/app/modules/job_progress/contract_workflow.py`
- Modify: `src/app/modules/job_progress/mail_workflow.py`
- Test: `tests/modules/test_job_progress_module_boundaries.py`
- Test: `tests/modules/test_contract_record_advanced_filter.py`
- Test: `tests/web/test_job_progress.py`

- [ ] Add failing boundary tests that reject writes to `signing_status` or `contract_review` through JobProgress.
- [ ] Add failing serializer tests proving the fields come from joined ContractRecord columns.
- [ ] Delete legacy assessment single-attachment fallback and old contract status mappings.
- [ ] Update filtering to typed contract columns and remove JSON expressions.
- [ ] Run focused tests and commit `refactor: remove contract state copies from job progress`.

### Task 4: Concurrency and query-warning regression

**Files:**
- Modify: `src/app/modules/job_progress/queries.py`
- Test: `tests/modules/test_contract_concurrency.py`
- Test: `tests/web/test_job_progress.py`

- [ ] Write a two-session contract version test expecting 409 for the stale writer.
- [ ] Add a warning-as-error regression around the candidate JobProgress list query.
- [ ] Fix the join/correlation that currently produces the cartesian-product warning.
- [ ] Run tests with `-W error::sqlalchemy.exc.SAWarning` and commit `fix: make contract and progress queries concurrency safe`.

### Task 5: Verification

```bash
env ALLOW_TEST_DATABASE_CLEANUP=true uv run pytest tests/modules/test_contract_state_model.py tests/modules/test_contract_policy.py tests/modules/test_contracting_workflow.py tests/modules/test_contract_concurrency.py tests/modules/test_job_progress_module_boundaries.py tests/modules/test_contract_record_advanced_filter.py tests/web/test_job_progress.py -q -W error::sqlalchemy.exc.SAWarning
uv run ruff check src/app/application/contracting.py src/app/modules/contract_record src/app/modules/job_progress tests
```

Expected: all commands exit 0 and emit no cartesian-product warning.
