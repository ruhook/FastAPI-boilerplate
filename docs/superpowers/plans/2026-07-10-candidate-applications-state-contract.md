# Candidate Applications State Contract Implementation Plan

> **For Codex:** Execute this plan with `superpowers:executing-plans`, following the red-green-refactor cycle in `superpowers:test-driven-development` for every behavior change.

**Goal:** Make the B-side recruitment state, C-side Applications list/detail UI, and the `712696307@qq.com` demo data use one candidate-facing state contract that matches the approved Word/Excel requirements.

**Architecture:** Add a pure backend presentation derivation module and serialize its output on every candidate application. The Applications list summary and `needs_action_only` filter derive from the same presentation object. The C-side stops re-deriving business state and only constructs layout-specific progress steps from API fields. Existing recruitment stages and JSON process fields remain unchanged.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, pytest, React 18, TypeScript, Vite, Arco Design, Lucide icons, Node assertion scripts, in-app Browser.

---

## Task 1: Define and test the backend candidate presentation matrix

**Files:**
- Create: `src/app/modules/job_progress/candidate_presentation.py`
- Create: `tests/modules/test_candidate_presentation.py`

1. Write parameterized failing tests for every approved matrix row: application review, assessment upload, assessment revision, assessment submitted, rate waiting, rate instructions, contract upload, contract revision, contract submitted, company-sealed waiting, task-group instructions, onboarded, rejected from each supported stage, replaced, and incomplete-data fallback.
2. Run `uv run pytest -q tests/modules/test_candidate_presentation.py` and confirm the import/behavior fails because the module does not exist yet.
3. Implement a pure `build_candidate_presentation(...)` function that accepts stage, assessment flag, process data, and current contract data and returns the nine approved presentation fields.
4. Keep `onboarding_status=已发砍价` authoritative for rate action; treat `salary_confirmed_at` as compatibility data only.
5. Run the focused tests until green, then run Ruff on the module and test.
6. Commit with `feat: add candidate presentation state contract`.

## Task 2: Serialize the contract, metadata, summaries, and exact action filter

**Files:**
- Modify: `src/app/modules/job_progress/schema.py`
- Modify: `src/app/modules/job_progress/service.py`
- Modify: `tests/web/test_my_applications.py`

1. Add failing web tests asserting list and detail items expose all presentation fields plus `country`, `country_label`, and `work_mode`.
2. Add failing tests for full-filter summary counts: `contract_uploads`, `other_actions`, `monitoring`, and `total_action_required`, including a data set larger than one page.
3. Add a failing exact-filter test showing `needs_action_only=true` includes assessment upload/revision, rate instructions, contract upload/revision, and task-group instructions while excluding every passive review state.
4. Run the focused tests with `ALLOW_TEST_DATABASE_CLEANUP=true` and confirm the expected schema/count/filter failures.
5. Add Pydantic presentation fields and a summary model to the candidate list/detail schemas.
6. Refactor `list_candidate_job_applications` to derive presentation once for all filtered rows, use it for exact action filtering and full-result summary counts, then paginate and serialize page assets.
7. Reuse the same serializer/helper in `get_candidate_job_application_detail` and include job location/work mode in list items.
8. Run focused tests, Ruff, and the existing candidate application web tests until green.
9. Commit with `feat: expose candidate application presentation summary`.

## Task 3: Make rejected late-stage records reversible without losing contract state

**Files:**
- Modify: `src/app/modules/job_progress/const.py`
- Modify: `src/app/modules/job_progress/service.py`
- Modify: `tests/web/test_job_progress.py`

1. Add failing tests for rejecting/restoring from `contract_pool` and `active`, mismatched restore rejection, and restoration of the active contract's previous status/end date.
2. Run the focused rejected-stage tests and confirm the new late-stage cases fail.
3. Allow rejected transitions back to `contract_pool` and `active`, while retaining the recorded-source-only guard.
4. On active rejection, store the previous contract status and end date in progress data before terminating the contract. On active restore, restore those values and clear the temporary metadata.
5. Run focused tests and Ruff until green.
6. Commit with `fix: restore rejected late-stage applications safely`.

## Task 4: Make the C-side consume the backend contract

**Files:**
- Modify: `candidate-web-next/src/apis/types.ts`
- Modify: `candidate-web-next/src/lib/candidateWorkflow.ts`
- Modify: `candidate-web-next/scripts/candidate-workflow-view.test.mjs`

1. Rewrite the workflow test fixtures around API presentation fields and add failing cases for all statuses/actions, stable assessment steps, submitted-review helpers, and `Engagement Ended`.
2. Run `node --experimental-strip-types scripts/candidate-workflow-view.test.mjs` and confirm failures against the old client-side derivation.
3. Add TypeScript presentation and summary types.
4. Simplify `buildCandidateWorkflowView` to map backend presentation fields into UI tones/modes/actions and only construct progress steps locally.
5. Ensure assessment-enabled jobs always include the assessment step, unknown presentation falls back safely, and no `Confirm Now`, `Join Now`, or `Replaced` copy remains.
6. Run the focused workflow test and `npm run build` until green.
7. Commit with `refactor: consume candidate presentation contract`.

## Task 5: Correct Applications list groups, counters, metadata, and routes

**Files:**
- Modify: `candidate-web-next/src/pages/MyApplicationsPage.tsx`
- Modify: `candidate-web-next/src/pages/ApplicationDetailPage.tsx`
- Modify: `candidate-web-next/scripts/candidate-workflow-design.test.mjs`
- Modify: `candidate-web-next/scripts/candidate-presentation-layout.test.mjs`

1. Add failing source/behavior assertions for exclusive server summary counters, location/work-mode metadata, `View Details` description routing, and `View Status`/upload/instruction stage routing.
2. Run the two focused Node scripts and confirm the old page-local counting and passive routing fail.
3. Render `Contract Uploads`, `Other Actions`, and `Monitoring` from `data.summary`; keep card groups page-local but mutually exclusive.
4. Add location and work mode to card metadata using existing display helpers and Lucide icons.
5. Route only `view_details` to `?view=details`; route every stage/status action to `?view=status`.
6. Make the detail page choose the initial pane from `candidate_action`/entry query, keep rejected and engagement-ended records read-only, and expose passive stage workspaces such as rate waiting and company-sealed onboarding preparation.
7. Run focused scripts and `npm run build` until green.
8. Commit with `feat: align Applications list actions and summaries`.

## Task 6: Align progress semantics and keep the desktop sidebar usable

**Files:**
- Modify: `candidate-web-next/src/components/candidate/WorkflowProgressPanel.tsx`
- Modify: `candidate-web-next/src/styles.css`
- Modify: `candidate-web-next/scripts/candidate-workflow-design.test.mjs`
- Modify: `candidate-web-next/scripts/candidate-presentation-layout.test.mjs`

1. Add failing assertions for `Submitted, awaiting review`, stable six/five-step totals, viewport-bounded sticky sidebar scrolling, and mobile `position: static`/normal overflow.
2. Run focused Node scripts and confirm the new assertions fail.
3. Show submitted assessment/contract current steps with `Submitted, awaiting review`; preserve deep/current/light icon treatments without checkbox interaction.
4. Bound the desktop sidebar with `max-height: calc(100vh - 36px)` and internal vertical scrolling; reset max-height and overflow in the existing mobile media query.
5. Run focused scripts and `npm run build` until green.
6. Commit with `fix: align application progress and sidebar behavior`.

## Task 7: Expand and safely reset the 712696307 demo data

**Files:**
- Modify: `src/scripts/run_candidate_my_jobs_demo.py`
- Modify: `tests/scripts/test_candidate_portal_demo_data.py`

1. Replace the old expected-view test with failing assertions for the complete approved state matrix, exact action-required keys, exact summary counts, more than ten auto-applied cases, and reset mail-task scoping.
2. Run `uv run pytest -q tests/scripts/test_candidate_portal_demo_data.py` and confirm the matrix/reset tests fail.
3. Expand job definitions and setup flows to cover assessment revision, contract revision, company-sealed onboarding preparation, late-stage rejection, engagement ended, and pagination.
4. Make reset delete mail tasks only when recipient matches and render context references a scoped demo job/application/progress; preserve unrelated mail for the same address.
5. Make script verification compare API presentation fields, summary counts, and the exact `needs_action_only` application set against an independent expected matrix.
6. Run focused script tests and Ruff until green.
7. Commit with `test: expand candidate portal state demo`.

## Task 8: Run the demo reset/generation and automated regression suite

**Files:**
- No expected source changes.

1. Run the documented demo command for `712696307@qq.com`; verify the script reports previous demo rows removed and one current application per matrix case generated.
2. Run all backend focused tests:
   `ALLOW_TEST_DATABASE_CLEANUP=true uv run pytest -q tests/modules/test_candidate_presentation.py tests/web/test_my_applications.py tests/web/test_job_progress.py tests/scripts/test_candidate_portal_demo_data.py`.
3. Run `uv run ruff check` on all changed backend Python files.
4. Run every `candidate-web-next/scripts/*.test.mjs` with Node and run `npm run build`.
5. Inspect `git diff --check` and both repository status outputs.

## Task 9: Browser acceptance and screenshot evidence

**Files:**
- Create screenshots under `/private/tmp/hr-state-audit/final/`.

1. Load the current Browser skill, initialize the in-app Browser, and reuse the signed-in C/B sessions.
2. On C-side Applications, verify all-page summary counts, exclusive groups, location/work mode, action labels, and second-page pagination.
3. Capture C-side screenshots for Assessment Under Review, Rate Instructions, Contract Revision, Onboarding Preparation, Rejected late-stage progress, Engagement Ended, and Successfully Onboarded.
4. On B-side, locate the same demo rows and verify stage/process/contract data matches each C-side presentation.
5. Validate sidebar position and internal scroll at desktop, then validate mobile normal flow and no overlap.
6. Save screenshots, finalize Browser tabs, and report exact automated/browser verification results plus any residual limitations.
