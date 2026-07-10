# Candidate Portal Demo Chinese State Titles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give all 16 candidate-portal acceptance jobs exact Chinese state-only titles and a normal Chinese role description without weakening demo-data ownership or verification safety.

**Architecture:** Keep scenario state in `PORTAL_JOB_DEFINITIONS`, add a hidden `candidate_portal_demo_case_key` marker to job data, and use the marker plus known identifiers for cleanup instead of visible title prefixes. Runtime acceptance verification filters API items by the job IDs created during the run, so status-only titles do not need a shared visible prefix.

**Tech Stack:** Python 3.12, SQLAlchemy async ORM, pytest, Ruff

## Global Constraints

- All 16 visible job titles are the exact approved Chinese state labels.
- Titles contain neither `C端验收` nor `葡语数据标注员`.
- Every definition uses `<p>负责葡萄牙语数据标注、内容质量检查与结果反馈，按照项目规范完成交付，并与项目团队保持及时沟通。</p>`.
- Scenario keys, state mutations, expected presentation fields, compensation, assessment settings, and application behavior remain unchanged.
- A Chinese status-only title never proves demo ownership by itself.
- Legacy `Candidate Portal Demo - ` jobs remain safely discoverable for archival.
- The database-mutating seed flow is not run by unit verification.

---

### Task 1: Chinese Acceptance Copy

**Files:**
- Modify: `src/scripts/run_candidate_my_jobs_demo.py:60-470`
- Test: `tests/scripts/test_candidate_portal_demo_data.py:1-115`

**Interfaces:**
- Produces: `CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION: str`
- Produces: exact `PORTAL_JOB_DEFINITIONS[*]["title"]` and shared `description`
- Preserves: `PORTAL_JOB_DEFINITIONS[*]["key"]` and `EXPECTED_CANDIDATE_VIEW_BY_KEY`

- [ ] **Step 1: Write the failing copy-contract test**

Add the following mapping and assertions:

```python
EXPECTED_DEMO_TITLES = {
    "fresh_apply_flow": "待申请",
    "application_review": "申请审核中",
    "assessment_action_required": "待上传测试题",
    "assessment_under_review": "测试题审核中",
    "rate_confirmation_waiting": "费率确认待通知",
    "rate_confirmation_action_required": "待查看费率说明",
    "signed_contract_action_required": "待上传签署合同",
    "signed_contract_under_review": "合同审核中",
    "task_group_action_required": "待查看入组说明",
    "successfully_onboarded": "已成功入职",
    "rejected": "已拒绝（申请审核阶段）",
    "assessment_revision_required": "测试题待重新提交",
    "signed_contract_revision_required": "合同待重新提交",
    "onboarding_preparation": "入职准备中",
    "rejected_late_stage": "已拒绝（合同阶段）",
    "engagement_ended": "合作已结束",
}


def test_candidate_portal_demo_jobs_use_exact_chinese_state_copy() -> None:
    definitions = run_candidate_my_jobs_demo.PORTAL_JOB_DEFINITIONS

    assert {item["key"]: item["title"] for item in definitions} == EXPECTED_DEMO_TITLES
    assert len({item["title"] for item in definitions}) == 16
    assert all("C端验收" not in item["title"] for item in definitions)
    assert all("葡语数据标注员" not in item["title"] for item in definitions)
    assert {
        item["description"] for item in definitions
    } == {run_candidate_my_jobs_demo.CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION}
```

Delete the obsolete `test_candidate_portal_demo_jobs_use_resettable_title_prefix` test.

- [ ] **Step 2: Run the focused test to verify RED**

Run:

```bash
uv run --extra dev pytest tests/scripts/test_candidate_portal_demo_data.py::test_candidate_portal_demo_jobs_use_exact_chinese_state_copy -q
```

Expected: FAIL because the definitions still use English prefixed titles and scenario descriptions.

- [ ] **Step 3: Implement the exact titles and neutral description**

Define:

```python
CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION = (
    "<p>负责葡萄牙语数据标注、内容质量检查与结果反馈，"
    "按照项目规范完成交付，并与项目团队保持及时沟通。</p>"
)
```

Replace all 16 title values with the exact mapping from Step 1 and set every
`description` to `CANDIDATE_PORTAL_DEMO_JOB_DESCRIPTION`. Do not alter any
other definition field.

- [ ] **Step 4: Run the focused test to verify GREEN**

Run:

```bash
uv run --extra dev pytest tests/scripts/test_candidate_portal_demo_data.py::test_candidate_portal_demo_jobs_use_exact_chinese_state_copy -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/scripts/run_candidate_my_jobs_demo.py tests/scripts/test_candidate_portal_demo_data.py
git commit -m "test: localize candidate demo job copy"
```

### Task 2: Marker-Based Demo Ownership

**Files:**
- Modify: `src/scripts/run_candidate_my_jobs_demo.py:60-70, 590-620, 730-850, 1125-1270`
- Test: `tests/scripts/test_candidate_portal_demo_data.py:85-180`

**Interfaces:**
- Produces: `CANDIDATE_PORTAL_DEMO_CASE_DATA_KEY = "candidate_portal_demo_case_key"`
- Produces: `LEGACY_CANDIDATE_PORTAL_DEMO_JOB_TITLE_PREFIX = "Candidate Portal Demo - "`
- Produces: `get_candidate_portal_demo_case_key(job: Job | SimpleNamespace) -> str`
- Produces: `is_candidate_portal_demo_owned_job(job: Job | SimpleNamespace) -> bool`
- Produces: `is_current_candidate_portal_demo_job(job: Job | SimpleNamespace) -> bool`

- [ ] **Step 1: Write failing ownership tests**

Replace the title-guard test with:

```python
def test_candidate_portal_demo_ownership_uses_marker_or_legacy_prefix() -> None:
    marked = SimpleNamespace(
        title="合同审核中",
        data={"candidate_portal_demo_case_key": "signed_contract_under_review"},
    )
    legacy = SimpleNamespace(title="Candidate Portal Demo - Rejected", data={})
    unrelated = SimpleNamespace(title="合同审核中", data={})

    assert run_candidate_my_jobs_demo.is_candidate_portal_demo_owned_job(marked)
    assert run_candidate_my_jobs_demo.is_current_candidate_portal_demo_job(marked)
    assert run_candidate_my_jobs_demo.is_candidate_portal_demo_owned_job(legacy)
    assert not run_candidate_my_jobs_demo.is_current_candidate_portal_demo_job(legacy)
    assert not run_candidate_my_jobs_demo.is_candidate_portal_demo_owned_job(unrelated)
```

Update the mail-task test so a Chinese title alone is unrelated, a known
`job.id` is accepted, and an old English-prefixed title remains accepted:

```python
scope = {
    "candidate_email": "712696307@qq.com",
    "job_ids": {101},
    "application_ids": {201},
    "progress_ids": {301},
}
```

- [ ] **Step 2: Run ownership tests to verify RED**

Run:

```bash
uv run --extra dev pytest tests/scripts/test_candidate_portal_demo_data.py -q
```

Expected: FAIL because marker helpers and `job_ids` mail scoping do not exist.

- [ ] **Step 3: Implement marker helpers**

Add:

```python
CANDIDATE_PORTAL_DEMO_CASE_DATA_KEY = "candidate_portal_demo_case_key"
LEGACY_CANDIDATE_PORTAL_DEMO_JOB_TITLE_PREFIX = "Candidate Portal Demo - "


def get_candidate_portal_demo_case_key(job: Any) -> str:
    data = job.data if isinstance(getattr(job, "data", None), dict) else {}
    return str(data.get(CANDIDATE_PORTAL_DEMO_CASE_DATA_KEY) or "")


def is_candidate_portal_demo_owned_job(job: Any) -> bool:
    return bool(get_candidate_portal_demo_case_key(job)) or str(
        getattr(job, "title", "") or ""
    ).startswith(LEGACY_CANDIDATE_PORTAL_DEMO_JOB_TITLE_PREFIX)


def is_current_candidate_portal_demo_job(job: Any) -> bool:
    current_keys = {str(item["key"]) for item in PORTAL_JOB_DEFINITIONS}
    return get_candidate_portal_demo_case_key(job) in current_keys
```

Remove `CANDIDATE_PORTAL_DEMO_JOB_TITLE_PREFIX` and
`is_current_candidate_portal_demo_job_title`.

- [ ] **Step 4: Use the marker for create, lookup, archive, and reset**

When building job data, include:

```python
CANDIDATE_PORTAL_DEMO_CASE_DATA_KEY: str(definition["key"]),
```

Locate a current job by owner and marker, never by a Chinese title. In
`archive_obsolete_candidate_portal_jobs`, load the demo owner's non-deleted
jobs and archive only jobs where
`is_candidate_portal_demo_owned_job(job) and not is_current_candidate_portal_demo_job(job)`.
In `reset_candidate_portal_demo_state`, union explicit `job_ids` with IDs
whose job passes `is_candidate_portal_demo_owned_job`.

- [ ] **Step 5: Make mail cleanup identifier-based**

Change `mail_task_targets_demo_scope` to consume `job_ids: set[int]`.
Accept a task when the normalized recipient matches and one of these holds:

```python
job_context.id in job_ids
job_context.title.startswith(LEGACY_CANDIDATE_PORTAL_DEMO_JOB_TITLE_PREFIX)
job_progress.id in progress_ids
"/my-jobs/{application_id}" appears in serialized render context
```

Do not accept a current Chinese title by itself.

- [ ] **Step 6: Run focused tests to verify GREEN**

Run:

```bash
uv run --extra dev pytest tests/scripts/test_candidate_portal_demo_data.py -q
```

Expected: all tests in the file pass.

- [ ] **Step 7: Commit**

```bash
git add src/scripts/run_candidate_my_jobs_demo.py tests/scripts/test_candidate_portal_demo_data.py
git commit -m "fix: track candidate demo ownership by marker"
```

### Task 3: Prefix-Free Runtime Verification

**Files:**
- Modify: `src/scripts/run_candidate_my_jobs_demo.py:570-610, 1995-2205`
- Test: `tests/scripts/test_candidate_portal_demo_data.py:40-90`

**Interfaces:**
- Produces: `build_candidate_summary_from_items(items: list[dict[str, Any]]) -> dict[str, int]`
- Consumes: seeded job IDs from `cases_by_key`

- [ ] **Step 1: Write a failing actual-item summary test**

Add:

```python
def test_candidate_portal_demo_builds_summary_from_filtered_api_items() -> None:
    cases = run_candidate_my_jobs_demo.build_expected_candidate_portal_cases()
    items = [
        {
            "candidate_action": case["expected_candidate_view"]["candidate_action"],
            "candidate_action_required": case["expected_candidate_view"][
                "candidate_action_required"
            ],
        }
        for case in cases
    ]

    assert run_candidate_my_jobs_demo.build_candidate_summary_from_items(items) == {
        "contract_uploads": 2,
        "other_actions": 4,
        "monitoring": 9,
        "total_action_required": 6,
    }
```

- [ ] **Step 2: Run the new test to verify RED**

Run:

```bash
uv run --extra dev pytest tests/scripts/test_candidate_portal_demo_data.py::test_candidate_portal_demo_builds_summary_from_filtered_api_items -q
```

Expected: FAIL because `build_candidate_summary_from_items` is missing.

- [ ] **Step 3: Implement actual-item summary calculation**

Add:

```python
def build_candidate_summary_from_items(items: list[dict[str, Any]]) -> dict[str, int]:
    contract_uploads = sum(item.get("candidate_action") == "upload_contract" for item in items)
    other_actions = sum(
        bool(item.get("candidate_action_required"))
        and item.get("candidate_action") != "upload_contract"
        for item in items
    )
    total_action_required = contract_uploads + other_actions
    return {
        "contract_uploads": contract_uploads,
        "other_actions": other_actions,
        "monitoring": len(items) - total_action_required,
        "total_action_required": total_action_required,
    }
```

- [ ] **Step 4: Remove keyword-prefix assumptions from live verification**

Fetch the Applications pages without a demo-title keyword. Build
`seeded_application_job_ids` from the 15 auto-applied cases and filter
`refreshed_payload["items"]` and `needs_action_payload["items"]` by
`job_id`. Compare `build_candidate_summary_from_items(refreshed_items)`
with `build_expected_candidate_summary(expected_cases)`.

For the page-size check, compare the paged response summary with the unfiltered
`refreshed_payload["summary"]`, because API summaries cover the complete
unfiltered candidate result. For the needs-action check, compare only the
filtered seeded titles and action flags; unrelated candidate applications must
not affect demo verification.

- [ ] **Step 5: Run the focused tests**

Run:

```bash
uv run --extra dev pytest tests/scripts/test_candidate_portal_demo_data.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Run lint and the relevant server regression**

Run:

```bash
uv run --extra dev ruff check src/scripts/run_candidate_my_jobs_demo.py tests/scripts/test_candidate_portal_demo_data.py
uv run --extra dev pytest tests/modules/test_candidate_presentation.py tests/scripts/test_candidate_portal_demo_data.py -q
```

Expected: Ruff exits 0 and both test files pass.

- [ ] **Step 7: Commit**

```bash
git add src/scripts/run_candidate_my_jobs_demo.py tests/scripts/test_candidate_portal_demo_data.py
git commit -m "test: verify candidate demo by seeded job ids"
```
