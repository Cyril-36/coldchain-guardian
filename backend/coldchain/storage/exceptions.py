"""Stable storage errors that do not expose provider exception details."""


class StorageError(RuntimeError):
    """Base class for storage failures."""


class IdempotencyConflict(StorageError):
    """An idempotency key was reused with a different request hash."""


class ConditionalWriteConflict(StorageError):
    """A conditional state update was rejected."""


class LeaseConflict(ConditionalWriteConflict):
    """A valid lease is held by another attempt."""


class ActiveRunConflict(ConditionalWriteConflict):
    """An operator already holds a valid active-run lease."""


class DailyLimitExceeded(ConditionalWriteConflict):
    """The UTC daily run limit has been reached."""


class ImmutableArtifactConflict(ConditionalWriteConflict):
    """An immutable artifact key already contains different bytes."""


class ArtifactNotFound(StorageError):
    """The requested immutable artifact does not exist."""


class ArtifactDigestMismatch(StorageError):
    """Stored artifact bytes do not match the expected digest."""


class RunNotFound(StorageError):
    """The requested run does not exist."""


class ReviewConflict(ConditionalWriteConflict):
    """A review does not reference the run's current report."""


class StorageUnavailable(StorageError):
    """The storage provider failed for a reason callers may retry."""
