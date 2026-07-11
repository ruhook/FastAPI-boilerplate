# Three-Client Contract Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Synchronize the final Payable, Payment, contract, referral, and version contracts across admin and candidate frontends, then verify all three repositories.

**Architecture:** Create isolated frontend worktrees, add typed API adapters first, then migrate pages and delete old adapters. Server contract tests remain the source of truth.

**Tech Stack:** React, TypeScript, Vitest, Vite, FastAPI contract tests

## Global Constraints

- Do not touch the existing dirty candidate worktree or its `JobDetailPage.tsx` change.
- Do not preserve old `/payment-records`, `/me/earnings`, Chinese persisted contract states, or fallback fields.
- TypeScript adapters convert snake_case wire DTOs into page models in one place.

---

### Task 1: Create isolated frontend worktrees

**Files:** Git metadata only.

- [ ] Record `git status --short --branch` and `git rev-parse HEAD` in both original frontend repositories.
- [ ] Create `/Users/ruanhaokang/workspace/hr/.worktrees/admin-business-layering` from the recorded admin SHA on `refactor/business-layering-overhaul`.
- [ ] Create `/Users/ruanhaokang/workspace/hr/.worktrees/candidate-business-layering` from the recorded candidate branch SHA on `refactor/business-layering-overhaul`.
- [ ] Verify the original candidate worktree still reports its pre-existing `src/pages/JobDetailPage.tsx` modification byte-for-byte with `git diff --stat` and `git diff -- src/pages/JobDetailPage.tsx`.

### Task 2: Admin settlement API contract

**Files:**
- Create: `admin-web-next/src/apis/payables.ts`
- Create: `admin-web-next/src/apis/payments.ts`
- Delete: `admin-web-next/src/apis/paymentRecords.ts`
- Test: `admin-web-next/src/apis/settlement.test.ts`

**Interfaces:** Typed adapters for list/sync/processing/reopen/cancel/pay/reverse and Payment list.

- [ ] Write failing mocked-http tests asserting exact new paths and snake_case payloads.
- [ ] Run `npm run test:unit -- src/apis/settlement.test.ts`; expect import failure.
- [ ] Implement wire DTOs, page models, and normalization; delete the old adapter after all imports move.
- [ ] Run the test; expect PASS.
- [ ] Commit `refactor: adopt settlement API contract` in the admin repository.

### Task 3: Admin Payable and Payment pages

**Files:**
- Create: `admin-web-next/src/pages/payments/PayablesPage.tsx`
- Create: `admin-web-next/src/pages/payments/PaymentsPage.tsx`
- Modify: `admin-web-next/src/App.tsx`
- Modify: `admin-web-next/src/layout/AppShell.tsx`
- Delete: `admin-web-next/src/pages/payments/PaymentRecordsPage.tsx`
- Modify: payment column/filter modules to use Payable or Payment names.
- Test: `admin-web-next/src/pages/payments/PayablesPage.test.tsx`
- Test: `admin-web-next/src/pages/payments/PaymentsPage.test.tsx`

- [ ] Write failing page tests for sync, state actions, partial payout result, immutable history, and reversal.
- [ ] Implement separate data sources while preserving existing permission key `流水记录`.
- [ ] Rename saved-filter and column-preference storage keys to new versioned keys; do not read old keys.
- [ ] Run page tests and commit `refactor: separate payable operations from payment history`.

### Task 4: Admin contract/referral/timesheet contract updates

**Files:**
- Modify: `admin-web-next/src/apis/contracts.ts`
- Modify: `admin-web-next/src/apis/jobProgress.ts`
- Modify: `admin-web-next/src/apis/referrals.ts`
- Modify: `admin-web-next/src/apis/timesheets.ts`
- Modify affected contract, referral, timesheet, and JobProgress pages/tests.

- [ ] Add failing adapter tests for typed contract enums, timesheet `version`, and referral derived totals.
- [ ] Remove JobProgress contract-write payload fields and Chinese-state comparisons.
- [ ] Send expected timesheet version on updates and display 409 refresh guidance.
- [ ] Run admin tests and build; commit `refactor: synchronize typed business contracts`.

### Task 5: Candidate Payment API and page

**Files:**
- Create: `candidate-web-next/src/apis/payments.ts`
- Delete: `candidate-web-next/src/apis/earnings.ts`
- Modify: `candidate-web-next/src/apis/types.ts`
- Modify: `candidate-web-next/src/pages/EarningsPage.tsx`
- Test: `candidate-web-next/src/pages/EarningsPage.test.tsx`

- [ ] Write a failing test asserting `/v1/me/payments` is used and only Payment rows appear.
- [ ] Implement typed Payment DTOs and migrate the page.
- [ ] Remove old earnings API and types.
- [ ] Run `npm test -- src/pages/EarningsPage.test.tsx`; expect PASS.
- [ ] Commit `refactor: read candidate earnings from payments`.

### Task 6: Candidate typed contract states

**Files:**
- Modify: `candidate-web-next/src/apis/types.ts`
- Modify: `candidate-web-next/src/lib/workspaces.ts`
- Modify: `candidate-web-next/src/lib/contractWorkspace.ts`
- Modify contract components and tests.

- [ ] Add failing tests using `changes_requested`, `approved`, `candidate_signed`, and `company_sealed`.
- [ ] Replace `Terminated`/`Expired` and Chinese-state comparisons with typed enum comparisons.
- [ ] Delete fallback optional contract state fields that no longer exist.
- [ ] Run candidate tests and commit `refactor: adopt typed contract workflow states`.

### Task 7: Three-repository verification

- [ ] Run the full program verification from the umbrella plan.
- [ ] Run `rg -n "payment-records|/me/earnings|contract_review.*待|Pending Activation|draft_uploaded|candidate_signed_uploaded|company_sealed_uploaded"` across all three worktrees; expected result is no production-code compatibility match.
- [ ] Run `git diff --check` and `git status --short` in all worktrees.
- [ ] Confirm each original repository worktree still contains only its pre-existing changes.
- [ ] Commit any verification-only test adjustments separately with `test: close business layering regression coverage`.

