# Job Progress Service Structural Split Design

## Goal

Split `src/app/modules/job_progress/service.py` into focused modules so that the job-progress domain can be understood, tested, and changed without loading a four-thousand-line service into context. This first pass is structural only: business behavior, API contracts, query semantics, transaction boundaries, locking, version checks, state transitions, and intentional authorization behavior must remain unchanged.

## Current State

`service.py` is 4,139 lines and currently owns several distinct responsibilities:

- advanced-filter normalization, SQL expression construction, and filter-record serialization;
- automation-rule evaluation and initial-stage resolution;
- shared model loading, row locking, optimistic-version validation, and serialization;
- admin and candidate read models;
- progress creation, stage movement, assessment, notes, language, and onboarding commands;
- mail configuration, mail-task creation, operation logging, and sent-mail synchronization;
- contract metadata, signing notification, and contract-asset workflows.

The module is imported by admin and candidate API routes, the mail event handler, talent-profile service, scripts, and tests. A direct hard cut would therefore mix structural movement with widespread caller changes and make behavior preservation harder to verify.

## Chosen Approach

Use a facade-first mechanical split. `service.py` remains as the stable public import surface and re-exports only supported public functions from focused implementation modules. It contains no business logic after the split.

This facade is the canonical domain entrypoint, not a temporary compatibility implementation. There will be no duplicate implementations, legacy fallbacks, conditional old/new paths, or deprecated aliases. Private helpers are imported from their owning modules in tests instead of being re-exported through the facade.

Two alternatives were rejected:

- Updating every caller to import implementation modules directly would expose internal layout as public API and create a large, difficult-to-review change.
- Extracting only filtering and serialization would reduce the file size but leave the command and workflow coupling that currently blocks further business-layer refactoring.

## Target Module Boundaries

### `service.py`

Canonical public facade. Re-exports the public query and command functions currently consumed by API routes, event handlers, scripts, other services, and tests. It owns no private helpers, database queries, or business decisions.

### `filtering.py`

Owns progress-filter normalization, stage and application SQL expressions, advanced-filter field definitions, advanced-filter record serialization, numeric and decimal normalization used by filtering, and filter-specific rejected-stage mapping.

It must not load database rows or call workflows.

### `automation.py`

Owns automation field-value construction, individual and aggregate rule evaluation, and initial-stage resolution.

It is a pure decision module apart from consuming already-loaded job and application field data. It must not create mail tasks or mutate progress rows.

### `state.py`

Owns shared persistence primitives: progress lookup, bulk model loading, locked-query construction, expected-version validation, UTC normalization, and small shared state mutations that are required by more than one workflow.

Assessment-invitation timestamp mutation belongs here because both the assessment workflow and mail synchronization need it. The module does not decide when an invitation should be sent.

### `serialization.py`

Owns shared conversion of progress, application snapshots, process data, assessment submissions, assets, identity attachments, contract records, and candidate presentation data into response dictionaries or schema models.

It may consume existing pure presentation helpers from `candidate_presentation.py`, but it must not issue writes or trigger external work.

### `queries.py`

Owns the four read workflows:

- admin job-progress listing;
- candidate application listing;
- candidate contract listing;
- candidate application detail.

It composes filtering, serialization, and read-only database queries. It must not contain command-side mutations.

### `commands.py`

Owns the general job-progress commands:

- create progress for an application;
- move recruitment stage;
- execute stage automation after assessment review;
- update note;
- update language;
- update onboarding data.

It coordinates state primitives, automation decisions, rejection restore behavior, operation logs, and mail triggering without owning their low-level implementations.

### `mail_workflow.py`

Owns candidate URL construction, job mail context, stage mail configuration, mail-task creation, mail operation logging, sign-contract notification, and synchronization from completed mail tasks.

It may call shared invitation-state primitives from `state.py`, but it must not import `commands.py` or `assessment_workflow.py`.

### `assessment_workflow.py`

Owns explicit assessment invitation marking, assessment review updates, and candidate assessment submission. It uses `state.py` for shared persistence behavior and may invoke the public stage-movement command, while `commands.py` must not import this module.

This direction prevents the existing assessment/mail relationship from becoming a circular dependency.

### `contract_workflow.py`

Owns contract-record updates and the candidate-signed, draft, and company-sealed contract asset workflows. Shared contract serialization stays in `serialization.py`; sign-contract mail notification stays in `mail_workflow.py`.

## Dependency Direction

Dependencies must flow downward only:

```text
service facade
    |
    +-- queries
    +-- commands
    +-- mail_workflow
    +-- assessment_workflow
    +-- contract_workflow
            |
            +-- state
            +-- serialization
            +-- filtering
            +-- automation
            +-- existing focused domain helpers
```

Rules:

- No implementation module imports from `service.py`.
- Leaf modules do not import workflow modules.
- `commands.py` may use `mail_workflow.py`; the reverse import is forbidden.
- `assessment_workflow.py` may invoke the stage-movement command; `commands.py` does not import `assessment_workflow.py`.
- Shared behavior is moved to a leaf module rather than resolved with local imports or duplicated functions.

## Public Interface

Existing public imports from `job_progress.service` remain valid. Function names, parameters, return values, raised domain exceptions, and asynchronous behavior remain unchanged.

The facade exposes only functions already used as public domain operations. Underscore-prefixed helpers are not facade exports. Tests that currently import private helpers from `service.py` switch to the owning focused module, making the real boundary explicit.

## Transaction And Error Invariants

Moving code must not change transaction ownership. Existing callers continue to control commit and rollback where they do today; a moved function must not add a commit, rollback, flush, or nested transaction solely because its file changed.

The following behavior is preserved exactly:

- row-lock acquisition and lock ordering;
- optimistic expected-version checks;
- status and stage transition validation;
- rejection and restore semantics;
- mail-task deduplication and operation-log timing;
- assessment invitation and submission timestamps;
- contract status and asset replacement behavior;
- exception types, messages, and not-found handling;
- result ordering, pagination, filtering, and candidate-facing presentation.

## Migration Sequence

The split is performed in independently reviewable batches:

1. Extract pure filtering and automation helpers.
2. Extract shared state and serialization helpers.
3. Extract read-only queries.
4. Extract mail workflow.
5. Extract general commands and assessment workflow while enforcing one-way dependencies.
6. Extract contract workflow.
7. Reduce `service.py` to the public facade and run final architectural checks.

Each batch moves existing code with the smallest import changes possible, updates focused tests, and is committed only after its targeted checks pass. Formatting or business cleanup unrelated to the move is deferred.

## Verification Strategy

Because this is a behavior-preserving refactor, existing tests are the primary contract. No test expectation is changed merely to make a move pass.

Verification includes:

- focused unit tests for automation rules, candidate presentation, language rules, and rejection restore;
- concurrency tests for locked updates and expected versions;
- admin and candidate web tests for list, detail, assessment, onboarding, and contract workflows;
- event-handler tests for sent-mail synchronization;
- import smoke tests proving the facade exposes the expected public operations;
- a cycle/import-boundary check proving implementation modules do not import the facade;
- the complete pytest suite;
- Ruff, mypy, Alembic single-head, and locked dependency checks used by the server baseline.

## Acceptance Criteria

- `service.py` is a thin public facade with no business implementation.
- Every moved function has exactly one implementation.
- Existing supported imports from `job_progress.service` still work.
- No implementation module imports from the facade.
- No circular imports or local-import workarounds are introduced.
- API schemas, database models, migrations, permissions, and business rules are unchanged.
- All targeted and full verification commands pass.

## Non-goals

- No redesign of recruitment stages, automation rules, assessment, mail, onboarding, or contract behavior.
- No API or schema redesign.
- No query optimization or SQL behavior change.
- No transaction redesign.
- No replacement of JSON-backed process data.
- No removal of intentional admin-data visibility or other intentional authorization behavior.
- No new compatibility layer, deprecation path, or dual implementation.
