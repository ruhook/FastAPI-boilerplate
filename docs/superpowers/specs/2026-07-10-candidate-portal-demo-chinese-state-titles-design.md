# Candidate Portal Demo Chinese State Titles Design

## Goal

Make every candidate-portal acceptance job immediately identifiable by its real workflow state while keeping the job-detail content representative of a normal role. The demo data must remain safe to rerun and must continue deriving candidate-facing state from actual recruitment data.

## Scope

- Update all 16 entries in `PORTAL_JOB_DEFINITIONS`.
- Change demo job titles to Chinese state-oriented titles.
- Replace scenario-explanation descriptions with one normal Chinese role description.
- Preserve every scenario key, workflow mutation, expected presentation contract, compensation value, assessment setting, and application behavior.
- Archive obsolete jobs created with either the old English prefix or the new Chinese prefix when the script is rerun.

## Title Format

Use:

```text
C端验收 - <中文状态> - 葡语数据标注员
```

The fixed prefix is machine-recognizable and the middle label is optimized for manual acceptance.

| Scenario key | Chinese state title |
| --- | --- |
| `fresh_apply_flow` | `C端验收 - 待申请 - 葡语数据标注员` |
| `application_review` | `C端验收 - 申请审核中 - 葡语数据标注员` |
| `assessment_action_required` | `C端验收 - 待上传测试题 - 葡语数据标注员` |
| `assessment_under_review` | `C端验收 - 测试题审核中 - 葡语数据标注员` |
| `rate_confirmation_waiting` | `C端验收 - 费率确认待通知 - 葡语数据标注员` |
| `rate_confirmation_action_required` | `C端验收 - 待查看费率说明 - 葡语数据标注员` |
| `signed_contract_action_required` | `C端验收 - 待上传签署合同 - 葡语数据标注员` |
| `signed_contract_under_review` | `C端验收 - 合同审核中 - 葡语数据标注员` |
| `task_group_action_required` | `C端验收 - 待查看入组说明 - 葡语数据标注员` |
| `successfully_onboarded` | `C端验收 - 已成功入职 - 葡语数据标注员` |
| `rejected` | `C端验收 - 已拒绝（申请审核阶段） - 葡语数据标注员` |
| `assessment_revision_required` | `C端验收 - 测试题待重新提交 - 葡语数据标注员` |
| `signed_contract_revision_required` | `C端验收 - 合同待重新提交 - 葡语数据标注员` |
| `onboarding_preparation` | `C端验收 - 入职准备中 - 葡语数据标注员` |
| `rejected_late_stage` | `C端验收 - 已拒绝（合同阶段） - 葡语数据标注员` |
| `engagement_ended` | `C端验收 - 合作已结束 - 葡语数据标注员` |

## Job Description

All 16 definitions use the same neutral role description:

```html
<p>负责葡萄牙语数据标注、内容质量检查与结果反馈，按照项目规范完成交付，并与项目团队保持及时沟通。</p>
```

The job description must not explain the seeded workflow state. Status and stage remain visible through the Applications presentation contract and the Chinese acceptance title.

## Legacy Cleanup

- Change the current title prefix to `C端验收 - `.
- Retain `Candidate Portal Demo - ` as a recognized legacy prefix.
- Cleanup queries and mail-task scope checks recognize both prefixes.
- Current-title checks still use the exact set generated from `PORTAL_JOB_DEFINITIONS`.
- Rerunning the script archives obsolete English-prefixed jobs and prevents duplicate visible applications.

## Data Flow

The title and description are display metadata only:

1. The scenario key selects the existing seed mutations.
2. The seed creates or updates the job using the Chinese title and neutral description.
3. Existing workflow setup produces the target B-side stage and process data.
4. The candidate presentation contract derives status, stage, action, and progress from that real data.
5. Script verification compares the API payload with `EXPECTED_CANDIDATE_VIEW_BY_KEY`.

No candidate-facing state is inferred from the Chinese title.

## Testing

- Assert the 16 scenario keys have the exact approved titles.
- Assert titles are unique and use the Chinese current prefix.
- Assert every definition uses the neutral Chinese role description.
- Assert current and legacy prefixes are recognized as demo-owned titles.
- Keep the independent expected presentation matrix unchanged.
- Run the focused demo-data tests and Ruff checks for the modified script and test file.

## Non-goals

- No change to production job titles.
- No change to recruitment stages, candidate status labels, actions, or progress calculation.
- No change to candidate or admin UI components.
- No execution of the database-mutating seed script as part of unit verification.
