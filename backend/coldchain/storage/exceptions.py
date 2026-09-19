"""Storage exception hierarchy for ColdChain Guardian.

These standard exceptions allow storage adapters (AWS DynamoDB/S3 or in-memory)
and API handlers to raise and map structured, typed errors with predictable
HTTP status codes and retry semantics without coupling to adapter implementations.

HTTP status mapping per docs/CONTRACTS.md:
- 404: NotFoundError (missing run, snapshot, report, or artifact)
- 409: StateConflictError, IdempotencyConflictError,
  ConditionalCheckFailedError, ReportNotReadyError
- 429: LimitExceededError, DailyLimitExceededError, ActiveRunLimitExceededError
- 503: TemporaryStorageError, QueueError, TemporaryEnqueueError (retryable)
"""

from __future__ import annotations


class StorageError(Exception):
    """Base exception for all ColdChain storage and queue failures."""

    status_code: int = 500
    retryable: bool = False


# ── 404 Not Found ─────────────────────────────────────────────────────────────


class NotFoundError(StorageError, KeyError):
    """Raised when a requested run, snapshot, report, or artifact does not exist."""

    status_code: int = 404
    retryable: bool = False


# ── 409 State / Concurrency / Idempotency Conflicts ──────────────────────────


class StateConflictError(StorageError, ValueError):
    """Base class for 409 conflicts (idempotency, concurrency race, report not ready)."""

    status_code: int = 409
    retryable: bool = False


class IdempotencyConflictError(StateConflictError):
    """Raised when an idempotency key is reused with a different request payload or hash."""


class ConditionalCheckFailedError(StateConflictError):
    """Raised when an optimistic concurrency or lease check fails (e.g., stale attempt)."""


class ReportNotReadyError(StateConflictError):
    """Raised when GET /runs/{id}/report or /download is requested before report is ready."""


# ── 429 Limit Conflicts ───────────────────────────────────────────────────────


class LimitExceededError(StorageError):
    """Base exception for 429 quota and concurrency limits."""

    status_code: int = 429
    retryable: bool = False


class DailyLimitExceededError(LimitExceededError):
    """Raised when daily run creation cap is reached (e.g., MAX_RUNS_PER_DAY=50)."""


class ActiveRunLimitExceededError(LimitExceededError):
    """Raised when concurrent active runs for an operator or account are exceeded."""


# ── 503 Temporary Failures ────────────────────────────────────────────────────


class TemporaryStorageError(StorageError):
    """Raised when a transient DynamoDB or S3 failure occurs."""

    status_code: int = 503
    retryable: bool = True


class QueueError(StorageError):
    """Base exception for queue publishing or polling failures."""

    status_code: int = 503
    retryable: bool = True


class TemporaryEnqueueError(QueueError, TemporaryStorageError):
    """Raised when dispatching to SQS fails temporarily and should be retried."""

    status_code: int = 503
    retryable: bool = True
