# Protected Assets Implementation Plan

## Goal

Keep every asset behind the authenticated Web/Admin APIs, bound memory use for uploads and batch downloads, and compensate storage writes when database persistence fails.

## Steps

1. Add regression tests proving serialization never exposes an OSS object URL, uploads are read in bounded chunks, oversized uploads stop early, and local keys cannot escape the configured storage root.
2. Make authenticated preview/download endpoints the only serialized asset URLs and validate local storage path containment.
3. Add configurable upload size/chunk limits and remove newly written local/OSS objects when the matching database insert fails.
4. Add regression tests for batch archive file-count and aggregate-byte limits.
5. Generate batch archives with a disk-backed temporary spool and stream the response in bounded chunks.
6. Document the production storage contract and run focused unit tests and static checks without touching the local database.

## Deferred follow-up

- Purpose-specific file signatures and MIME allowlists require an explicit product matrix for resumes, contracts, rich-text media, mail attachments, and timesheets.
- Retention-based physical deletion and orphan reconciliation require a separate lifecycle job because deleting an object before its database transaction commits can leave a live row without content.
