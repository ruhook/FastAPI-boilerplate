# Candidate Applications State Contract Design

## Goal

Make the B-side recruitment data, C-side Applications list, application detail page, and demo-data script use one candidate-facing state contract. The change must remove contradictory actions, keep progress truthful, and make every supported B-side state reproducible with the `712696307@qq.com` demo account.

## Chosen Approach

The backend owns candidate-facing presentation state. Each application response exposes a normalized presentation object derived from recruitment stage, process data, assessment configuration, and the current contract record. The C-side renders this object and keeps only layout-specific progress-step construction.

This is preferred over patching more frontend branches because the existing API filter, card counts, detail routing, and demo assertions already disagree. A database-driven state-machine rewrite is deliberately out of scope; existing recruitment stages and JSON process fields remain the source data.

## Candidate Presentation Contract

Each candidate application list and detail item returns:

```text
candidate_status: under_review | action_required | rejected | onboarded | engagement_ended
candidate_stage: application_review | assessment_file | rate_confirmation | signed_contract | task_group | onboarding_completed
candidate_action: view_details | view_status | upload_assessment | view_rate_instructions | upload_contract | view_joining_instructions
candidate_action_required: boolean
candidate_status_label: string
candidate_stage_title: string
candidate_stage_body: string
candidate_action_label: string
```

The backend derives these fields once. The list summary, `needs_action_only` filter, C-side grouping, buttons, and detail workspace selection all consume the same result.

## State Matrix

| B-side condition | Candidate status | Candidate stage | Candidate action |
| --- | --- | --- | --- |
| Pending screening; assessment not successfully sent | Under Review | Application Review | View Details |
| Assessment successfully sent; no candidate submission | Action Required | Assessment File | Upload Assessment |
| Assessment requires resubmission | Action Required | Assessment File | Upload Assessment |
| Assessment submitted; awaiting review | Under Review | Assessment File | View Status |
| Screening passed; rate instructions not sent | Under Review | Rate Confirmation | View Status |
| `onboarding_status=已发砍价` | Action Required | Rate Confirmation | View Instructions |
| Draft contract available; no candidate-signed contract, or contract revision required | Action Required | Signed Contract | Upload Contract |
| Candidate-signed contract submitted; awaiting approval | Under Review | Signed Contract | View Status |
| Company-sealed contract complete; waiting for onboarding guide | Under Review | Task Group | View Status |
| `onboarding_status=已发大礼包`; no onboarding date | Action Required | Task Group | View Instructions |
| Active with onboarding date | Successfully Onboarded | Onboarding Completed | View Status |
| Rejected | Rejected | Preserved pre-rejection stage | View Details |
| Replaced | Engagement Ended | Preserved final stage | View Details |

`salary_confirmed_at` remains a compatibility field but does not independently create a candidate action. `onboarding_status=已发砍价` is the authoritative current trigger until a dedicated rate-response workflow exists.

## Interaction Rules

### Applications list

- `View Details` always opens the job description.
- `View Status` opens the current stage workspace.
- Upload and instruction actions open the current stage workspace.
- Rate and task-group controls must describe what they actually do. Until the portal can submit a rate decision or join a group, use `View Instructions`; do not show a self-link called `Confirm Now` or a modal trigger called `Join Now`.
- List metadata includes location, work mode, and application date.

### Detail page

- Entry from `View Details` starts on the job description.
- Entry from any stage/status action starts on the stage workspace.
- The sidebar `View Details` switches to the job description; `Go Back` returns to the originating stage workspace or Applications list.
- Rejected and engagement-ended applications always open the read-only job description.

### Progress

- If the job has assessment enabled, `Assessment File` is present from the first application view. Total steps therefore remain stable: six with assessment, five without it.
- Percentage is `current step number / total step count`, matching `Step 2 of 6 = 33%`. It is not described as completed-stage percentage.
- Submitted assessment and contract stages remain the current step until B-side approval. Their helper text is `Submitted, awaiting review`; they are not marked completed early.
- Completed, current, and pending steps use the same semantic icons with deep, active, and light visual treatments. The progress list is not clickable.
- On desktop, the complete sidebar remains available while the right pane scrolls. Because the sidebar can exceed the viewport, it gets a viewport-bounded internal scroll area. On mobile it returns to normal document flow.

## Summary Counts And Filtering

The Applications response includes counts across the full filtered result, not only the current page:

```text
contract_uploads
other_actions
monitoring
total_action_required
```

The visible three counters are exclusive: `Contract Uploads`, `Other Actions`, and `Monitoring`, so they sum to the filtered application total. `needs_action_only=true` uses `candidate_action_required` from the same presentation contract and includes assessment upload/revision, rate instructions, contract upload/revision, and task-group instructions while excluding passive review states.

## Rejection And Restore

- Rejection preserves `rejected_from_stage` and all candidate-facing milestone data.
- Restore is allowed only to the recorded source stage.
- Restore supports pending screening, assessment review, screening passed, contract pool, and active.
- When an active record is rejected, store the contract's previous status and end date in progress data before terminating it.
- Restoring to active reinstates those stored contract values, removes the temporary restore metadata, and preserves the operation log trail.
- Replaced remains a separate terminal business outcome and is presented as `Engagement Ended`; it is not silently relabeled Rejected.

## Demo Data And Reset Safety

- Preserve the candidate account and reusable resume asset. Reset only demo-owned applications, progress rows, contracts, and mail tasks.
- Mail-task deletion must require both the demo candidate recipient and a demo job/application reference. It must not delete unrelated mail sent to the same email address.
- The seed creates cases for every row in the state matrix, including assessment revision, contract revision, onboarding preparation, rejection from a late contract stage, engagement ended, and more than one Applications page.
- Seed verification compares API presentation fields and summary counts against an independent expected matrix. It also verifies `needs_action_only` returns exactly the action-required cases.
- Re-running the seed soft-deletes previous demo workflow records, archives obsolete demo jobs, and produces one current application per current demo case.

## Error Handling

- Unknown or incomplete data falls back to `Under Review / Application Review / View Details`; it must never expose an upload action without the required asset or invitation.
- A rejected record missing a valid source stage remains rejected and cannot be restored until B-side data is corrected.
- Candidate action labels and workspaces must never imply a successful external action unless an API mutation or confirmed external navigation occurred.

## Testing

- Backend unit tests cover every presentation-matrix row as pure derivation tests.
- Backend web tests cover list/detail serialization, exclusive summary counts, exact needs-action filtering, and rejection restore from contract pool and active.
- C-side workflow tests consume API presentation fields and verify list labels, entry routing, progress steps, submitted helper text, and engagement-ended copy.
- Layout tests assert desktop sidebar viewport bounds and mobile normal-flow reset.
- Browser acceptance uses `712696307@qq.com` to capture Applications, Assessment Under Review, Rate Instructions, Onboarding Preparation, Rejected late-stage progress, and matching B-side rows.

## Non-goals

- No candidate Accept/Decline rate API in this change.
- No actual task-group join or resend-email mutation in this change.
- No migration of existing recruitment-stage storage to a new database state machine.
- No redesign of the accepted Applications card visual language.
