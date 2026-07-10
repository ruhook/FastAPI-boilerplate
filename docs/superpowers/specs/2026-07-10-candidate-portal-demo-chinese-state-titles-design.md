# Candidate Portal Demo Chinese State Titles Design

## Goal

Make every candidate-portal acceptance job immediately identifiable by its real workflow state while keeping the job-detail content representative of a normal role. The demo data must remain safe to rerun and must continue deriving candidate-facing state from actual recruitment data.

## Scope

- Update all 16 entries in `PORTAL_JOB_DEFINITIONS`.
- Change demo job titles to Chinese state-oriented titles.
- Replace scenario-explanation descriptions with one normal Chinese role description.
- Preserve every scenario key, workflow mutation, expected presentation contract, compensation value, assessment setting, and application behavior.
- Add a hidden demo-case marker to each seeded job so status-only titles remain safe to rerun.
- Archive obsolete jobs identified by the hidden marker or the old English prefix when the script is rerun.

## Title Format

Use the Chinese state label as the complete visible job title. Do not add a
`C端验收` prefix or a role-name suffix.

| Scenario key | Chinese state title |
| --- | --- |
| `fresh_apply_flow` | `待申请` |
| `application_review` | `申请审核中` |
| `assessment_action_required` | `待上传测试题` |
| `assessment_under_review` | `测试题审核中` |
| `rate_confirmation_waiting` | `费率确认待通知` |
| `rate_confirmation_action_required` | `待查看费率说明` |
| `signed_contract_action_required` | `待上传签署合同` |
| `signed_contract_under_review` | `合同审核中` |
| `task_group_action_required` | `待查看入组说明` |
| `successfully_onboarded` | `已成功入职` |
| `rejected` | `已拒绝（申请审核阶段）` |
| `assessment_revision_required` | `测试题待重新提交` |
| `signed_contract_revision_required` | `合同待重新提交` |
| `onboarding_preparation` | `入职准备中` |
| `rejected_late_stage` | `已拒绝（合同阶段）` |
| `engagement_ended` | `合作已结束` |

## Job Description

All 16 definitions use the same neutral role description:

```html
<p>负责葡萄牙语数据标注、内容质量检查与结果反馈，按照项目规范完成交付，并与项目团队保持及时沟通。</p>
```

The job description must not explain the seeded workflow state. Status and stage remain visible through the Applications presentation contract and the Chinese acceptance title.

## Demo Ownership And Legacy Cleanup

- Store the scenario key in each job's data under
  `candidate_portal_demo_case_key`.
- Locate current seeded jobs by owner plus this marker, not by their visible
  title.
- Retain `Candidate Portal Demo - ` only as a recognized legacy prefix.
- Cleanup queries and mail-task scope checks recognize the hidden marker,
  known job/application/progress identifiers, and the legacy prefix.
- A status-only Chinese title is never sufficient proof that a record is
  demo-owned.
- Rerunning the script archives obsolete English-prefixed jobs, migrates
  current scenarios to marker-owned Chinese titles, and prevents duplicate
  visible applications.

## Data Flow

The title and description are display metadata only:

1. The scenario key selects the existing seed mutations.
2. The seed locates the marker-owned scenario job and creates or updates it
   using the Chinese title and neutral description.
3. Existing workflow setup produces the target B-side stage and process data.
4. The candidate presentation contract derives status, stage, action, and progress from that real data.
5. Script verification compares the API payload with `EXPECTED_CANDIDATE_VIEW_BY_KEY`.

No candidate-facing state is inferred from the Chinese title.

## Testing

- Assert the 16 scenario keys have the exact approved titles.
- Assert titles are unique, contain the exact approved state labels, and have
  no acceptance prefix or role-name suffix.
- Assert every definition uses the neutral Chinese role description.
- Assert the hidden marker identifies current demo jobs.
- Assert legacy English-prefixed jobs remain eligible for safe archival.
- Assert a status-only Chinese title is not treated as demo ownership by
  itself.
- Keep the independent expected presentation matrix unchanged.
- Run the focused demo-data tests and Ruff checks for the modified script and test file.

## Non-goals

- No change to production job titles.
- No change to recruitment stages, candidate status labels, actions, or progress calculation.
- No change to candidate or admin UI components.
- No execution of the database-mutating seed script as part of unit verification.
