# Reliable Events And Mail Delivery Plan

**Goal:** Ensure database mutations do not lose their asynchronous work, and ensure failed handlers remain retryable instead of being acknowledged.

**Architecture:** Mail-task creation writes an `event_outbox` row in the same transaction. A dispatcher claims due outbox rows with a lease, publishes the stable event id to Redis Streams, and records success or bounded retry. Redis messages are ACKed only after the event manager reports successful handler completion. Mail processing locks the task row and treats already-final tasks as idempotent no-ops.

## Tasks

1. Add `event_outbox` model and Alembic revision `20260710_000042` with stable id, payload, status, attempts, availability, lease, publication/failure timestamps, and error summary.
2. Add outbox enqueue/claim/publish/failure services and a dispatcher loop in `event_consumer.py`.
3. Replace direct mail-task `send_event` calls with transactional outbox insertion; remove request-path commit/dispatch flags.
4. Make event-manager handler failures propagate and make the Redis client ACK only on success; reclaim stale pending messages with `XAUTOCLAIM`.
5. Lock mail tasks before processing and no-op final/active claims to prevent concurrent sends; add an explicit `delivery_unknown` state for ambiguous SMTP failures.
6. Verify pure state-machine tests, focused lint/compile, one Alembic head, and database tests only after explicit disposable-database approval.
