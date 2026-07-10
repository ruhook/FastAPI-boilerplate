# Job Progress Service Structural Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 4,139-line job-progress service implementation with focused modules behind one thin, stable `service.py` facade without changing business behavior.

**Architecture:** Extract pure leaf helpers first, then shared state and serialization, followed by read queries and command workflows. Keep `job_progress.service` as the canonical public import surface, enforce one-way imports with an AST-based boundary test, and move tests that patch private implementation details to the module that owns those details.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy async ORM, Pydantic, MySQL 8.4, pytest, Ruff, mypy baseline checks, Alembic, uv.

## Global Constraints

- Work on the current `codex/candidate-applications-state-contract` branch. Do not create another branch or worktree.
- This pass is structural only. Preserve business rules, API paths, request/response schemas, query semantics, ordering, pagination, status transitions, side-effect timing, and exception behavior.
- Preserve current transaction ownership, flush points, row-lock order, optimistic-version checks, and caller-controlled commit/rollback behavior.
- Preserve the intentional rule that Admin job-progress data and Admin attachment data are available to every authenticated Admin account.
- Keep every supported public import from `src.app.modules.job_progress.service` valid.
- Do not retain private helpers in the facade, duplicate implementations, deprecated aliases, fallbacks, or old/new conditional paths.
- Do not optimize queries, fix unrelated type debt, rename response fields, change messages, or perform formatting cleanup outside moved code.
- Existing tests are characterization contracts. Do not weaken an assertion to make a mechanical move pass.
- The direct job-progress mypy baseline is currently 24 errors and is outside the established core mypy gate. This plan must not claim full job-progress type cleanliness or expand scope to fix those errors.
- Commit after every independently testable extraction batch.

## Locked File Structure

- Create `src/app/modules/job_progress/normalization.py`: shared pure normalizers and datetime formatting.
- Create `src/app/modules/job_progress/filtering.py`: advanced-filter constants, SQL expressions, and record shaping.
- Create `src/app/modules/job_progress/automation.py`: automation-rule evaluation and initial-stage resolution.
- Create `src/app/modules/job_progress/state.py`: progress loading, locking, version validation, shared lookups, and shared invitation-state mutation.
- Create `src/app/modules/job_progress/serialization.py`: application, process, asset, contract, and candidate-presentation serialization.
- Create `src/app/modules/job_progress/queries.py`: admin progress list and candidate list/detail read workflows.
- Create `src/app/modules/job_progress/mail_workflow.py`: stage mail, sign-contract mail, and mail-result synchronization.
- Create `src/app/modules/job_progress/commands.py`: creation, stage movement, automation execution, note, language, and onboarding commands.
- Create `src/app/modules/job_progress/assessment_workflow.py`: invitation marking, review updates, and candidate assessment upload.
- Create `src/app/modules/job_progress/contract_workflow.py`: contract metadata and contract file workflows.
- Modify `src/app/modules/job_progress/service.py`: imports plus explicit `__all__` only after the final batch.
- Create `tests/modules/test_job_progress_module_boundaries.py`: public facade and import-direction contract.

---

### Task 1: Extract normalization, filtering, and automation leaves

**Files:**
- Create: `src/app/modules/job_progress/normalization.py`
- Create: `src/app/modules/job_progress/filtering.py`
- Create: `src/app/modules/job_progress/automation.py`
- Modify: `src/app/modules/job_progress/service.py:106-766`
- Modify: `src/app/modules/job_progress/service.py:1251-1257`
- Modify: `src/app/modules/job_progress/service.py:1616-1695`
- Modify: `tests/modules/test_job_progress_automation_rules.py:7`

**Interfaces:**
- Produces in `normalization.py`: `_normalize_text`, `_normalize_language_values`, `_has_asset_id`, `_has_assessment_attachment`, `_normalize_number`, `_normalize_decimal`, `_ensure_utc_datetime`, `_serialize_progress_datetime` with their existing signatures and bodies.
- Produces in `filtering.py`: `ADVANCED_FILTER_BACKEND_STAGE_MAP`, all filter helpers currently at lines 201-532, and `_build_progress_advanced_filter_record` currently at lines 1616-1695.
- Produces in `automation.py`: `_build_field_value_map`, `_evaluate_automation_rule`, `_evaluate_automation_rules`, `_resolve_initial_stage`, and `_field_row_value` with their existing signatures and bodies.
- Consumes: existing `advanced_filter`, `job`, `job_progress.const`, `job_progress.schema`, and `CandidateApplicationFieldValue` types only; none of the new modules imports `service.py`.

- [ ] **Step 1: Run the characterization tests before moving code**

Run:

```bash
.venv/bin/pytest -q \
  tests/modules/test_job_progress_automation_rules.py \
  tests/modules/test_job_progress_language_rules.py \
  tests/modules/test_job_progress_concurrency.py
```

Expected: all selected tests pass before extraction.

- [ ] **Step 2: Create the normalization leaf by moving the exact existing definitions unchanged**

Move these definitions from `service.py` to `normalization.py`, preserving their bodies and annotations:

```python
def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_language_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [item for item in (_normalize_text(item) for item in value) if item]
    normalized = _normalize_text(value)
    return [normalized] if normalized else []


def _has_asset_id(value: Any) -> bool:
    return _normalize_text(value).lower() not in {"", "0", "none", "null"}


def _has_assessment_attachment(progress: JobProgress) -> bool:
    progress_data = progress.data or {}
    if _has_asset_id(progress_data.get(JobProgressDataKey.ASSESSMENT_ATTACHMENT_ASSET_ID.value)):
        return True
    raw_submissions = progress_data.get(JobProgressDataKey.ASSESSMENT_SUBMISSIONS.value)
    if not isinstance(raw_submissions, list):
        return False
    return any(isinstance(item, dict) and _has_asset_id(item.get("asset_id")) for item in raw_submissions)


def _normalize_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _normalize_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).strip())
    except Exception:
        return None


def _ensure_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _serialize_progress_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()
```

Use only these dependency imports in addition to standard-library types:

```python
from .const import JobProgressDataKey
from .model import JobProgress
```

- [ ] **Step 3: Create the filtering leaf by moving the exact filter implementation unchanged**

Move `ADVANCED_FILTER_BACKEND_STAGE_MAP` and these exact definitions to `filtering.py`:

```python
_map_backend_stage_to_progress_stage
_build_rejected_from_stage_progress_stage_sql_expression
_serialize_rejected_from_stage_for_filter
_normalize_progress_filter_field_kind
_build_progress_stage_sql_expression
_build_progress_application_field_sql_expression
_build_progress_json_text_expression
_build_job_languages_sql_expression
_build_progress_assessment_attachment_filter_expression
_build_progress_contract_sql_expression
_build_progress_advanced_filter_field_map
_serialize_filter_record_datetime
_build_progress_advanced_filter_record
```

Replace local normalization calls with imports from the new leaf:

```python
from .normalization import _ensure_utc_datetime, _normalize_text
```

- [ ] **Step 4: Create the automation leaf by moving the exact rule implementation unchanged**

Move these definitions to `automation.py`:

```python
_build_field_value_map
_evaluate_automation_rule
_evaluate_automation_rules
_resolve_initial_stage
_field_row_value
```

The cross-leaf import is exactly:

```python
from .normalization import _normalize_number, _normalize_text
```

- [ ] **Step 5: Rewire the remaining service implementation and the private automation test**

Add direct imports to `service.py` for every moved helper still used by code that has not yet moved. Remove the original definitions instead of leaving wrappers.

Change the test import to the real owner:

```python
from src.app.modules.job_progress.automation import _evaluate_automation_rules
```

- [ ] **Step 6: Run focused tests and Ruff**

Run:

```bash
.venv/bin/pytest -q \
  tests/modules/test_job_progress_automation_rules.py \
  tests/modules/test_job_progress_language_rules.py \
  tests/modules/test_job_progress_concurrency.py
.venv/bin/ruff check --no-fix \
  src/app/modules/job_progress/normalization.py \
  src/app/modules/job_progress/filtering.py \
  src/app/modules/job_progress/automation.py \
  src/app/modules/job_progress/service.py \
  tests/modules/test_job_progress_automation_rules.py
```

Expected: all selected tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 7: Commit Task 1**

```bash
git add src/app/modules/job_progress tests/modules/test_job_progress_automation_rules.py
git commit -m "refactor: extract job progress decision helpers"
```

---

### Task 2: Extract shared state, locking, and lookup primitives

**Files:**
- Create: `src/app/modules/job_progress/state.py`
- Modify: `src/app/modules/job_progress/service.py:541-611`
- Modify: `src/app/modules/job_progress/service.py:875-943`
- Modify: `src/app/modules/job_progress/service.py:1148-1201`
- Modify: `tests/modules/test_job_progress_concurrency.py:6-9`

**Interfaces:**
- Produces: `_get_company_name_map_by_job_ids`, `_get_company_name_map_by_company_ids`, `_get_project_name_map_by_job_ids`, `_get_project_name_map_by_project_ids`.
- Produces: `_has_assessment_invitation(progress: JobProgress) -> bool` and `_mark_assessment_invited(progress: JobProgress, *, invited_at: datetime | None = None, mail_task_id: int | None = None, sent_at: datetime | None = None) -> bool`.
- Produces: `get_job_progress_by_application_id`, `get_job_progress_models`, `build_locked_job_progress_query`, and `ensure_expected_progress_versions` with unchanged signatures.
- Consumes: normalization helpers and ORM/domain models only; it never imports a workflow or the facade.

- [ ] **Step 1: Move shared read and mutation primitives unchanged**

Create `state.py` with the exact existing bodies for:

```python
_get_company_name_map_by_job_ids
_get_company_name_map_by_company_ids
_get_project_name_map_by_job_ids
_get_project_name_map_by_project_ids
_has_assessment_invitation
_mark_assessment_invited
get_job_progress_by_application_id
get_job_progress_models
build_locked_job_progress_query
ensure_expected_progress_versions
```

Import normalization only through:

```python
from .normalization import _normalize_text, _serialize_progress_datetime
```

- [ ] **Step 2: Remove the original bodies and import state primitives into the remaining service implementation**

Do not add wrappers. The intermediate `service.py` imports the four non-underscore state functions so existing callers remain valid while later code is still being extracted.

- [ ] **Step 3: Point the concurrency test at the owning module**

Replace its import with:

```python
from src.app.modules.job_progress.state import (
    build_locked_job_progress_query,
    ensure_expected_progress_versions,
)
```

- [ ] **Step 4: Run state tests and Ruff**

Run:

```bash
.venv/bin/pytest -q tests/modules/test_job_progress_concurrency.py
.venv/bin/ruff check --no-fix \
  src/app/modules/job_progress/state.py \
  src/app/modules/job_progress/service.py \
  tests/modules/test_job_progress_concurrency.py
```

Expected: four concurrency tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/app/modules/job_progress/state.py src/app/modules/job_progress/service.py tests/modules/test_job_progress_concurrency.py
git commit -m "refactor: extract job progress state primitives"
```

---

### Task 3: Extract shared serialization

**Files:**
- Create: `src/app/modules/job_progress/serialization.py`
- Modify: `src/app/modules/job_progress/service.py:116-134`
- Modify: `src/app/modules/job_progress/service.py:895-915`
- Modify: `src/app/modules/job_progress/service.py:1203-1615`
- Modify: `tests/modules/test_contract_record_team_leader_fields.py`

**Interfaces:**
- Produces: `serialize_job_progress(progress: JobProgress) -> dict[str, Any]`.
- Produces: candidate compensation, stage visibility, application snapshot, process data/assets, assessment records, identity attachment, contract asset/data, and candidate-presentation helpers listed below.
- Consumes: `normalization.py`, existing `candidate_presentation.py`, existing schemas/models, and read-only asset/user data.
- Does not write database rows, trigger mail, create logs, or import a workflow/facade.

- [ ] **Step 1: Move serialization constants and functions unchanged**

Move `CONTRACT_PROCESS_DATA_KEYS`, `CONTRACT_PROCESS_ASSET_KEYS`, and these exact definitions into `serialization.py`:

```python
serialize_job_progress
_build_candidate_compensation_label
_should_show_candidate_compensation
_serialize_application_snapshot
_serialize_progress_process_data
_serialize_application_assets
_extract_process_asset_ids
_get_assessment_submission_records
_serialize_assessment_submission_records
_serialize_process_data
_serialize_process_assets
_extract_id_attachment_asset_id
_list_id_attachment_asset_ids_by_user
_serialize_identity_attachment_asset
_extract_contract_record_asset_ids
_build_contract_asset_read
_serialize_contract_record_data
_get_candidate_visible_stage
_get_candidate_visible_stage_label
_build_candidate_presentation_for_progress
```

The helper imports from lower modules are:

```python
from .normalization import _ensure_utc_datetime, _normalize_text
from .state import _has_assessment_invitation
```

- [ ] **Step 2: Remove original definitions and import serializers into the remaining service implementation**

Keep `serialize_job_progress` available from intermediate `service.py`. Import private serializers only for the not-yet-extracted queries and workflows that call them.

- [ ] **Step 3: Move the private contract serializer test to the owner module**

Change:

```python
from src.app.modules.job_progress import service as job_progress_service
```

to:

```python
from src.app.modules.job_progress import serialization as job_progress_serialization
```

and call:

```python
payload = job_progress_serialization._serialize_contract_record_data(
    progress=_progress(),
    contract_record=_contract_record(),
    asset_map={},
    current_company_name="ByteDance",
    current_project_name="Any Project",
).model_dump()
```

- [ ] **Step 4: Run serialization and presentation tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/modules/test_candidate_presentation.py \
  tests/modules/test_contract_record_team_leader_fields.py \
  tests/modules/test_job_progress_language_rules.py
.venv/bin/ruff check --no-fix \
  src/app/modules/job_progress/serialization.py \
  src/app/modules/job_progress/service.py \
  tests/modules/test_contract_record_team_leader_fields.py
```

Expected: all selected tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/app/modules/job_progress/serialization.py src/app/modules/job_progress/service.py tests/modules/test_contract_record_team_leader_fields.py
git commit -m "refactor: extract job progress serialization"
```

---

### Task 4: Extract admin and candidate queries

**Files:**
- Create: `src/app/modules/job_progress/queries.py`
- Modify: `src/app/modules/job_progress/service.py:1698-2268`

**Interfaces:**
- Produces unchanged public functions: `list_job_progress`, `list_candidate_job_applications`, `list_candidate_contracts`, `get_candidate_job_application_detail`.
- Consumes: filtering, normalization, serialization, shared state lookups, ORM models, schemas, and existing read services.
- Performs no writes and does not import any workflow module or the facade.

- [ ] **Step 1: Run the query characterization tests before extraction**

Run:

```bash
.venv/bin/pytest -q \
  tests/core/test_candidate_application_streaming.py \
  tests/web/test_my_applications.py
```

Expected: selected streaming and candidate list/detail/contract tests pass.

- [ ] **Step 2: Move the four query functions unchanged**

Create `queries.py` with the exact bodies and signatures of:

```python
list_job_progress
list_candidate_job_applications
list_candidate_contracts
get_candidate_job_application_detail
```

Use direct leaf imports. In particular, query code must consume `_build_progress_advanced_filter_field_map` from `filtering.py`, company/project maps from `state.py`, and all response shaping from `serialization.py`; it must not copy any helper.

- [ ] **Step 3: Re-export the four functions from intermediate service.py**

Remove the original bodies and add:

```python
from .queries import (
    get_candidate_job_application_detail,
    list_candidate_contracts,
    list_candidate_job_applications,
    list_job_progress,
)
```

- [ ] **Step 4: Run query tests and Ruff**

Run:

```bash
.venv/bin/pytest -q \
  tests/core/test_candidate_application_streaming.py \
  tests/web/test_my_applications.py \
  tests/web/test_job_progress.py
.venv/bin/ruff check --no-fix \
  src/app/modules/job_progress/queries.py \
  src/app/modules/job_progress/service.py
```

Expected: all selected tests pass. The two already-known warnings may remain; no new warning or failure is accepted. Ruff reports `All checks passed!`.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/app/modules/job_progress/queries.py src/app/modules/job_progress/service.py
git commit -m "refactor: extract job progress queries"
```

---

### Task 5: Extract mail workflows

**Files:**
- Create: `src/app/modules/job_progress/mail_workflow.py`
- Modify: `src/app/modules/job_progress/service.py:767-873`
- Modify: `src/app/modules/job_progress/service.py:945-1056`
- Modify: `src/app/modules/job_progress/service.py:2640-2681`
- Modify: `src/app/modules/job_progress/service.py:3355-3536`

**Interfaces:**
- Produces private coordination helpers: `_build_candidate_assessment_url`, `_get_job_mail_context`, `_build_candidate_contract_upload_url`, `_contains_contract_upload_url_variable`, `_get_stage_mail_config`, `_record_stage_mail_operation`, `_trigger_stage_mail_task`.
- Produces unchanged public function `sync_assessment_sent_at_from_mail_task(mail_task_id: int) -> bool`.
- Produces unchanged public function `notify_job_progress_sign_contract(*, job_id: int, progress_ids: list[int], admin_user_id: int, db: AsyncSession, account_id: int, template_id: int | None, signature_id: int | None, subject: str, body_html: str, cc_recipients: list[MailRecipient], bcc_recipients: list[MailRecipient], attachment_asset_ids: list[int], render_context: dict[str, Any]) -> dict[str, Any]`.
- Consumes: state primitives, filtering's JSON SQL expression, serialization, mail-task/template services, and operation logging.
- Must not import `commands.py`, `assessment_workflow.py`, `contract_workflow.py`, or `service.py`.

- [ ] **Step 1: Move mail helpers and public workflows unchanged**

Create `mail_workflow.py` and move the exact definitions listed in the Interfaces block. Keep `local_session`, task creation, flushes, operation-log calls, and exception handling at their existing points.

- [ ] **Step 2: Rewire remaining command code and facade exports**

The remaining code in `service.py` imports `_trigger_stage_mail_task`. Public imports remain available through:

```python
from .mail_workflow import notify_job_progress_sign_contract, sync_assessment_sent_at_from_mail_task
```

- [ ] **Step 3: Run mail synchronization and recruitment workflow tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/web/test_job_progress.py \
  tests/event/test_mail_outbox_contract.py
.venv/bin/ruff check --no-fix \
  src/app/modules/job_progress/mail_workflow.py \
  src/app/modules/job_progress/service.py \
  src/app/event/handlers/mail.py
```

Expected: selected tests pass, including both successful assessment-mail synchronization cases, and Ruff reports `All checks passed!`.

- [ ] **Step 4: Commit Task 5**

```bash
git add src/app/modules/job_progress/mail_workflow.py src/app/modules/job_progress/service.py
git commit -m "refactor: extract job progress mail workflows"
```

---

### Task 6: Extract general commands

**Files:**
- Create: `src/app/modules/job_progress/commands.py`
- Modify: `src/app/modules/job_progress/service.py:1059-1146`
- Modify: `src/app/modules/job_progress/service.py:2271-2577`
- Modify: `src/app/modules/job_progress/service.py:2881-3176`
- Modify: `tests/modules/test_job_progress_onboarding_timestamps.py`
- Modify: `tests/modules/test_job_progress_clear_editable_fields.py`

**Interfaces:**
- Produces unchanged public functions: `create_job_progress_for_application`, `move_job_progress_stage`, `execute_job_progress_assessment_automation`, `update_job_progress_note`, `update_job_progress_language`, `update_job_progress_onboarding`.
- Keeps `_format_current_process_datetime` and `_format_current_process_date` private to `commands.py`.
- Consumes: automation, normalization, state, mail workflow, rejection restore, language rules, referral profile service, notifications, and operation logging.
- `commands.py` may import `mail_workflow.py`; mail workflow must never import commands.

- [ ] **Step 1: Move the six command functions and two time-format helpers unchanged**

Create `commands.py` with the exact existing signatures and bodies. Preserve the stage-move `# noqa: C901`, every validation branch, every mutation order, every `flush`, mail trigger timing, rejection restore behavior, and operation log payload.

- [ ] **Step 2: Re-export commands from service.py and remove the originals**

Add:

```python
from .commands import (
    create_job_progress_for_application,
    execute_job_progress_assessment_automation,
    move_job_progress_stage,
    update_job_progress_language,
    update_job_progress_note,
    update_job_progress_onboarding,
)
```

- [ ] **Step 3: Update tests that monkeypatch command implementation globals**

In `tests/modules/test_job_progress_onboarding_timestamps.py`, use:

```python
from src.app.modules.job_progress import commands as job_progress_commands
```

and patch/call `job_progress_commands.datetime`, `job_progress_commands.get_job_progress_models`, `job_progress_commands.create_operation_log`, and `job_progress_commands.update_job_progress_onboarding`.

In `tests/modules/test_job_progress_clear_editable_fields.py`, add the same commands import and change only the onboarding test to patch/call `job_progress_commands`; leave contract tests on the intermediate service until Task 8.

- [ ] **Step 4: Run command characterization tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/modules/test_job_progress_automation_rules.py \
  tests/modules/test_job_progress_language_rules.py \
  tests/modules/test_job_progress_onboarding_timestamps.py \
  tests/modules/test_job_progress_rejection_restore.py \
  tests/modules/test_job_progress_clear_editable_fields.py \
  tests/web/test_job_progress.py
.venv/bin/ruff check --no-fix \
  src/app/modules/job_progress/commands.py \
  src/app/modules/job_progress/service.py \
  tests/modules/test_job_progress_onboarding_timestamps.py \
  tests/modules/test_job_progress_clear_editable_fields.py
```

Expected: selected tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 5: Commit Task 6**

```bash
git add src/app/modules/job_progress/commands.py src/app/modules/job_progress/service.py tests/modules/test_job_progress_onboarding_timestamps.py tests/modules/test_job_progress_clear_editable_fields.py
git commit -m "refactor: extract job progress commands"
```

---

### Task 7: Extract assessment workflows

**Files:**
- Create: `src/app/modules/job_progress/assessment_workflow.py`
- Modify: `src/app/modules/job_progress/service.py:2580-2637`
- Modify: `src/app/modules/job_progress/service.py:2684-2878`
- Modify: `src/app/modules/job_progress/service.py:3539-3679`

**Interfaces:**
- Produces unchanged public functions: `mark_job_progress_assessment_invited`, `update_job_progress_assessment_review`, `submit_job_progress_assessment`.
- Consumes: shared state, normalization, serialization, asset upload, notifications, and operation logging.
- Must not import the facade. If a future assessment path needs stage movement, it may import the command directly; commands must not import assessment workflow.

- [ ] **Step 1: Move the three assessment functions unchanged**

Create `assessment_workflow.py` and preserve invitation timestamp semantics, reviewer-scope checks, assessment submission history, asset upload arguments, notifications, flush points, and response models exactly.

- [ ] **Step 2: Re-export assessment functions from service.py and remove originals**

Use:

```python
from .assessment_workflow import (
    mark_job_progress_assessment_invited,
    submit_job_progress_assessment,
    update_job_progress_assessment_review,
)
```

- [ ] **Step 3: Run assessment tests and Ruff**

Run:

```bash
.venv/bin/pytest -q \
  tests/web/test_job_assessment_upload.py \
  tests/web/test_my_applications.py \
  tests/web/test_job_progress.py
.venv/bin/ruff check --no-fix \
  src/app/modules/job_progress/assessment_workflow.py \
  src/app/modules/job_progress/service.py
```

Expected: all selected tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 4: Commit Task 7**

```bash
git add src/app/modules/job_progress/assessment_workflow.py src/app/modules/job_progress/service.py
git commit -m "refactor: extract job progress assessment workflows"
```

---

### Task 8: Extract contract workflows

**Files:**
- Create: `src/app/modules/job_progress/contract_workflow.py`
- Modify: `src/app/modules/job_progress/service.py:136-154`
- Modify: `src/app/modules/job_progress/service.py:613-620`
- Modify: `src/app/modules/job_progress/service.py:3179-3352`
- Modify: `src/app/modules/job_progress/service.py:3682-4139`
- Modify: `tests/modules/test_job_progress_clear_editable_fields.py`

**Interfaces:**
- Produces unchanged public functions: `update_job_progress_contract_record`, `submit_job_progress_candidate_signed_contract`, `upload_job_progress_contract_draft`, `upload_job_progress_company_sealed_contract`.
- Keeps `CONTRACT_RECORD_FIELD_STAGE_MAP` and `_validate_contract_record_update_stage` private to the contract workflow.
- Consumes: normalization, state, serialization, contract-record service, asset upload, notifications, and operation logging.
- Must not import mail workflow, commands, queries, or facade.

- [ ] **Step 1: Move contract validation and four workflows unchanged**

Create `contract_workflow.py` with the exact existing constant, validator, signatures, and bodies. Preserve file module/owner metadata, contract-record upsert fields, asset replacement behavior, timestamps, response models, flush points, and notification/log ordering.

- [ ] **Step 2: Re-export contract workflows and remove their originals**

Add:

```python
from .contract_workflow import (
    submit_job_progress_candidate_signed_contract,
    update_job_progress_contract_record,
    upload_job_progress_company_sealed_contract,
    upload_job_progress_contract_draft,
)
```

- [ ] **Step 3: Point contract monkeypatch tests at the owning module**

In `tests/modules/test_job_progress_clear_editable_fields.py`, use both owners:

```python
from src.app.modules.job_progress import commands as job_progress_commands
from src.app.modules.job_progress import contract_workflow as job_progress_contract_workflow
```

The first two tests patch/call `job_progress_contract_workflow.get_job_progress_models`, `upsert_contract_record_for_progress`, `create_operation_log`, and `update_job_progress_contract_record`. The onboarding test continues to use `job_progress_commands`.

- [ ] **Step 4: Run contract and candidate workflow tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/modules/test_contract_record_team_leader_fields.py \
  tests/modules/test_job_progress_clear_editable_fields.py \
  tests/web/test_job_assessment_upload.py \
  tests/web/test_my_applications.py \
  tests/web/test_job_progress.py
.venv/bin/ruff check --no-fix \
  src/app/modules/job_progress/contract_workflow.py \
  src/app/modules/job_progress/service.py \
  tests/modules/test_job_progress_clear_editable_fields.py
```

Expected: selected tests pass and Ruff reports `All checks passed!`.

- [ ] **Step 5: Commit Task 8**

```bash
git add src/app/modules/job_progress/contract_workflow.py src/app/modules/job_progress/service.py tests/modules/test_job_progress_clear_editable_fields.py
git commit -m "refactor: extract job progress contract workflows"
```

---

### Task 9: Lock the thin facade and module boundaries

**Files:**
- Modify: `src/app/modules/job_progress/service.py`
- Create: `tests/modules/test_job_progress_module_boundaries.py`

**Interfaces:**
- Produces: the exact 24-operation facade contract in `service.__all__`.
- Enforces: no function/class definitions in the facade and no implementation-module import from `job_progress.service`.
- Preserves: all external API/event/script/service imports currently found by `rg 'job_progress\.service' src tests`.

- [ ] **Step 1: Write the failing facade and import-boundary tests**

Create:

```python
import ast
import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_database_cleanup

MODULE_DIR = Path(__file__).resolve().parents[2] / "src/app/modules/job_progress"
IMPLEMENTATION_MODULES = {
    "assessment_workflow",
    "automation",
    "commands",
    "contract_workflow",
    "filtering",
    "mail_workflow",
    "normalization",
    "queries",
    "serialization",
    "state",
}
PUBLIC_OPERATIONS = {
    "build_locked_job_progress_query",
    "create_job_progress_for_application",
    "ensure_expected_progress_versions",
    "execute_job_progress_assessment_automation",
    "get_candidate_job_application_detail",
    "get_job_progress_by_application_id",
    "get_job_progress_models",
    "list_candidate_contracts",
    "list_candidate_job_applications",
    "list_job_progress",
    "mark_job_progress_assessment_invited",
    "move_job_progress_stage",
    "notify_job_progress_sign_contract",
    "serialize_job_progress",
    "submit_job_progress_assessment",
    "submit_job_progress_candidate_signed_contract",
    "sync_assessment_sent_at_from_mail_task",
    "update_job_progress_assessment_review",
    "update_job_progress_contract_record",
    "update_job_progress_language",
    "update_job_progress_note",
    "update_job_progress_onboarding",
    "upload_job_progress_company_sealed_contract",
    "upload_job_progress_contract_draft",
}


def test_service_is_thin_explicit_public_facade() -> None:
    service = importlib.import_module("src.app.modules.job_progress.service")
    tree = ast.parse((MODULE_DIR / "service.py").read_text(encoding="utf-8"))

    assert set(service.__all__) == PUBLIC_OPERATIONS
    assert all(callable(getattr(service, name)) for name in PUBLIC_OPERATIONS)
    assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for node in tree.body)
    assert not hasattr(service, "_evaluate_automation_rules")
    assert not hasattr(service, "_serialize_contract_record_data")


def test_implementation_modules_import_without_cycles_or_facade_dependency() -> None:
    for module_name in sorted(IMPLEMENTATION_MODULES):
        importlib.import_module(f"src.app.modules.job_progress.{module_name}")
        tree = ast.parse((MODULE_DIR / f"{module_name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").endswith("job_progress.service")
                assert node.module != "service"
                if (node.module or "").endswith("job_progress") or node.module is None:
                    assert all(alias.name != "service" for alias in node.names)
            if isinstance(node, ast.Import):
                assert all(not alias.name.endswith("job_progress.service") for alias in node.names)
```

- [ ] **Step 2: Run the new test and verify the intermediate facade contract fails**

Run:

```bash
.venv/bin/pytest -q tests/modules/test_job_progress_module_boundaries.py
```

Expected: `test_service_is_thin_explicit_public_facade` fails until the facade is reduced and `__all__` is defined.

- [ ] **Step 3: Replace service.py with the exact public facade**

```python
from .assessment_workflow import (
    mark_job_progress_assessment_invited,
    submit_job_progress_assessment,
    update_job_progress_assessment_review,
)
from .commands import (
    create_job_progress_for_application,
    execute_job_progress_assessment_automation,
    move_job_progress_stage,
    update_job_progress_language,
    update_job_progress_note,
    update_job_progress_onboarding,
)
from .contract_workflow import (
    submit_job_progress_candidate_signed_contract,
    update_job_progress_contract_record,
    upload_job_progress_company_sealed_contract,
    upload_job_progress_contract_draft,
)
from .mail_workflow import notify_job_progress_sign_contract, sync_assessment_sent_at_from_mail_task
from .queries import (
    get_candidate_job_application_detail,
    list_candidate_contracts,
    list_candidate_job_applications,
    list_job_progress,
)
from .serialization import serialize_job_progress
from .state import (
    build_locked_job_progress_query,
    ensure_expected_progress_versions,
    get_job_progress_by_application_id,
    get_job_progress_models,
)

__all__ = [
    "build_locked_job_progress_query",
    "create_job_progress_for_application",
    "ensure_expected_progress_versions",
    "execute_job_progress_assessment_automation",
    "get_candidate_job_application_detail",
    "get_job_progress_by_application_id",
    "get_job_progress_models",
    "list_candidate_contracts",
    "list_candidate_job_applications",
    "list_job_progress",
    "mark_job_progress_assessment_invited",
    "move_job_progress_stage",
    "notify_job_progress_sign_contract",
    "serialize_job_progress",
    "submit_job_progress_assessment",
    "submit_job_progress_candidate_signed_contract",
    "sync_assessment_sent_at_from_mail_task",
    "update_job_progress_assessment_review",
    "update_job_progress_contract_record",
    "update_job_progress_language",
    "update_job_progress_note",
    "update_job_progress_onboarding",
    "upload_job_progress_company_sealed_contract",
    "upload_job_progress_contract_draft",
]
```

- [ ] **Step 4: Run boundary, public-caller, and import smoke tests**

Run:

```bash
.venv/bin/pytest -q \
  tests/modules/test_job_progress_module_boundaries.py \
  tests/modules/test_job_progress_concurrency.py \
  tests/core/test_candidate_application_streaming.py \
  tests/web/test_job_assessment_upload.py \
  tests/web/test_my_applications.py \
  tests/web/test_job_progress.py
.venv/bin/python -c "from src.app.main_web import app as web_app; from src.app.main_admin import app as admin_app; from src.app.event.handlers.mail import handle_mail_task_sent; assert web_app and admin_app and handle_mail_task_sent"
```

Expected: all selected tests pass and the import-smoke command exits 0.

- [ ] **Step 5: Run complete verification**

Run:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check --no-fix src tests
.venv/bin/mypy \
  src/app/core/config.py \
  src/app/core/health.py \
  src/app/core/setup.py \
  src/app/core/db/database.py \
  src/app/core/utils/cache.py \
  src/app/event \
  src/app/modules/event_outbox \
  src/app/modules/auth_refresh_session \
  src/app/modules/admin/mail_task \
  src/app/modules/admin/mail_account \
  src/app/modules/admin/role \
  src/app/modules/assets \
  --config-file pyproject.toml
cd src
../.venv/bin/alembic -c alembic.ini heads
cd ..
uv lock --check
git diff --check
```

Expected:

- Full pytest remains at least the current `335 passed, 1 skipped` baseline with no failures; only the two documented existing warnings may remain.
- Ruff and the established core mypy gate exit 0.
- Alembic prints exactly `20260710_000047 (head)`.
- `uv lock --check` and `git diff --check` exit 0.
- `service.py` contains only imports and `__all__`; `rg -n '^(async )?def ' src/app/modules/job_progress/service.py` prints nothing.

- [ ] **Step 6: Commit Task 9**

```bash
git add src/app/modules/job_progress/service.py tests/modules/test_job_progress_module_boundaries.py
git commit -m "refactor: make job progress service a facade"
```

---

## Self-Review Results

- **Spec coverage:** Every module and invariant from the approved design has an extraction task, targeted verification, and a final boundary contract. The additional `normalization.py` leaf is included because dependency analysis showed the same pure helpers are consumed across filtering, automation, commands, mail, assessment, and contract code.
- **Placeholder scan:** Clean. Existing large function bodies are referenced by exact function name and current line range so the mechanical move does not duplicate thousands of implementation lines in the plan.
- **Type consistency:** Public signatures remain the existing signatures. The final facade list contains all 24 non-underscore operations currently defined by `service.py`, including state helpers used by tests/scripts and all API/event/service call sites found by repository search.
