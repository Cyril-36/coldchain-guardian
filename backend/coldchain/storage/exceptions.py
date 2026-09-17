"""Storage exception hierarchy for ColdChain Guardian.

These standard exceptions allow storage adapters (AWS DynamoDB/S3 or in-memory)
to raise structured, typed errors without coupling to adapter implementations.
"""

from __future__ import annotations


class StorageError(Exception):
    """Base exception for all ColdChain storage failures."""


class IdempotencyConflictError(StorageError, ValueError):
    """Raised when an idempotency key is reused with a different request payload or hash."""


class ConditionalCheckFailedError(StorageError, ValueError):
    """Raised when an optimistic concurrency or lease check fails (e.g., stale attempt)."""


class NotFoundError(StorageError, KeyError):
    """Raised when a requested run, snapshot, or report does not exist."""
