# Business Service Decomposition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace oversized mixed-responsibility services with focused command/query/workflow modules and thin HTTP routes.

**Architecture:** Move code without changing approved behavior, guarded by characterization tests first. Return typed Pydantic DTOs instead of `dict[str, Any]` at service boundaries.

**Tech Stack:** FastAPI, SQLAlchemy async, Pydantic, Pytest, mypy

## Global Constraints

- No facade or import compatibility module remains after callers move.
- Cross-module writes live only in `src/app/application`.
- Each extraction is behavior-preserving and starts with characterization tests.

---

### Task 1: Project timesheet decomposition

**Files:**
- Create: `src/app/modules/project_timesheet_record/commands.py`
- Create: `src/app/modules/project_timesheet_record/queries.py`
- Create: `src/app/modules/project_timesheet_record/analytics.py`
- Create: `src/app/modules/project_timesheet_record/serialization.py`
- Delete: `src/app/modules/project_timesheet_record/service.py`
- Test: `tests/modules/test_project_timesheet_boundaries.py`

- [ ] Add characterization tests for admin workspace, candidate workspace, analytics, create/update/delete, and option queries.
- [ ] Add an import-boundary test disallowing `queries -> application` and `analytics -> commands` dependencies.
- [ ] Extract serialization, then queries, analytics, and commands; update all routes/imports.
- [ ] Run timesheet/admin/web tests and commit `refactor: split project timesheet responsibilities`.

### Task 2: Talent profile decomposition

**Files:**
- Create: `src/app/modules/talent_profile/application_submission.py`
- Create: `src/app/modules/talent_profile/merge.py`
- Create: `src/app/modules/talent_profile/commands.py`
- Create: `src/app/modules/talent_profile/queries.py`
- Create: `src/app/modules/talent_profile/serialization.py`
- Modify: `src/app/modules/talent_profile/model.py`
- Create: `src/migrations/versions/20260711_000052_talent_status_column.py`
- Delete: `src/app/modules/talent_profile/service.py`
- Test: `tests/modules/test_talent_profile_boundaries.py`

- [ ] Write characterization tests for application submission, merge strategies, pool list, detail aggregation, and status updates.
- [ ] Write a failing model test for indexed `status_override` and absence of JSON fallback.
- [ ] Extract focused modules, update callers, add migration, and remove old service.
- [ ] Run talent/admin application tests and commit `refactor: split talent profile use cases`.

### Task 3: Contract and job service decomposition

**Files:**
- Create: `src/app/modules/contract_record/queries.py`
- Create: `src/app/modules/contract_record/serialization.py`
- Delete: `src/app/modules/contract_record/service.py`
- Create: `src/app/modules/job/commands.py`
- Create: `src/app/modules/job/queries.py`
- Create: `src/app/modules/job/policy.py`
- Create: `src/app/modules/job/serialization.py`
- Delete: `src/app/modules/job/service.py`
- Test: `tests/modules/test_business_module_boundaries.py`

- [ ] Add behavior and import-boundary tests before moving code.
- [ ] Extract contract query/serialization code around the commands/policy created in the prior plan.
- [ ] Extract job validation policy, queries, commands, and serialization.
- [ ] Update admin and web routes and commit `refactor: split contract and job services`.

### Task 4: Thin API routes and transaction ownership

**Files:**
- Modify: `src/app/api/v1/jobs.py`
- Modify: `src/app/api/v1/me.py`
- Modify: `src/app/api/v1/web_users.py`
- Modify: `src/app/api/v1/assets.py`
- Modify: `src/app/modules/admin/company/service.py`
- Modify: `src/app/modules/admin/dictionary/service.py`
- Test: `tests/core/test_api_layer_boundaries.py`
- Test: `tests/core/test_transaction_ownership.py`

- [ ] Write AST/import tests rejecting direct ORM mutation/query in selected route modules, excluding the intentional asset authorization query until it is moved to an asset policy function.
- [ ] Write nested-use-case tests proving company/dictionary IntegrityError translation does not globally rollback prior session work.
- [ ] Move route business logic to commands/queries and replace service rollback with savepoints or propagated errors.
- [ ] Run core/API tests and commit `refactor: enforce application and transaction boundaries`.

### Task 5: Typed interfaces and mypy gate

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/core/test_repository_contracts.py`

- [ ] Replace service `dict[str, Any]` return annotations in changed modules with named DTOs.
- [ ] Add changed business modules to the project mypy command/documented CI gate.
- [ ] Run `uv run mypy` on every changed module and fix all errors without blanket ignores.
- [ ] Commit `chore: type business module contracts`.

### Task 6: Verification

```bash
env ALLOW_TEST_DATABASE_CLEANUP=true uv run pytest tests/modules tests/admin tests/web tests/core/test_api_layer_boundaries.py tests/core/test_transaction_ownership.py -q
uv run ruff check src tests
uv run mypy src/app/application src/app/modules/payable src/app/modules/payment src/app/modules/project_timesheet_record src/app/modules/referral src/app/modules/contract_record src/app/modules/job_progress src/app/modules/talent_profile src/app/modules/job
```

Expected: all commands exit 0; deleted service modules have no imports in `rg "modules\.(project_timesheet_record|talent_profile|contract_record|job)\.service" src tests`.
