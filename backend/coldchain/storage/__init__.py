"""Storage interfaces and protocol definitions for ColdChain Guardian.

Source of truth: docs/CONTRACTS.md §Storage interface
This package defines the StorageProtocol, QueueSenderProtocol, and ArtifactSignerProtocol
that backend persistence and queue adapters implement (e.g., Harshith's AWS DynamoDB,
S3, and SQS adapters) without coupling to any in-memory adapter.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from coldchain.contracts.enums import PublicStage, ReviewDecision, RunStatus
from coldchain.contracts.schemas import (
    ArtifactRef,
    ClaimResult,
    Report,
    Review,
    Run,
    RunSummary,
    Snapshot,
    StageEvent,
)

from .aws import AwsStorage
from .exceptions import (
    ActiveRunLimitExceededError,
    ConditionalCheckFailedError,
    DailyLimitExceededError,
    IdempotencyConflictError,
    LimitExceededError,
    NotFoundError,
    QueueError,
    ReportNotReadyError,
    StateConflictError,
    StorageError,
    TemporaryEnqueueError,
    TemporaryStorageError,
)
from .memory import MemoryStorage


@runtime_checkable
class QueueSenderProtocol(Protocol):
    """Abstract queue-sender boundary for publishing investigation jobs (e.g., AWS SQS).

    Distinguishes message delivery from storage state transitions so network/queue
    failures preserve retryable pending_enqueue status without regressing states.
    """

    def send_run(
        self,
        run_id: str,
        snapshot_id: str,
        schema_version: str = "1.0",
    ) -> None:
        """Publish an investigation job message to the worker queue.

        Raises:
            QueueError: on temporary or terminal dispatch failure.
        """
        ...

    def enqueue_run(
        self,
        run_id: str,
        snapshot_id: str,
        schema_version: str = "1.0",
    ) -> None:
        """Alias for send_run."""
        ...


@runtime_checkable
class ArtifactSignerProtocol(Protocol):
    """Abstract boundary for signing short-lived download URLs for stored artifacts."""

    def create_report_download_url(
        self,
        report_ref: ArtifactRef,
        expires_in_seconds: int = 300,
    ) -> str:
        """Generate a short-lived signed download URL (default 5 minutes / 300 seconds)."""
        ...


@runtime_checkable
class StorageProtocol(Protocol):
    """Abstract storage operations contract per docs/CONTRACTS.md."""

    def create_or_get_run(
        self,
        owner_sub: str,
        idempotency_key: str,
        request_hash: str,
        metadata: dict[str, Any],
    ) -> Run:
        """Atomically reserve or retrieve a run in pending_enqueue with stage preparing."""
        ...

    def attach_snapshot(
        self,
        run_id: str,
        snapshot_ref: ArtifactRef,
    ) -> None:
        """Persist snapshot reference on the pending run prior to queue delivery."""
        ...

    def put_snapshot(self, snapshot: Snapshot) -> ArtifactRef:
        """Persist an immutable snapshot object and return its key and SHA-256."""
        ...

    def get_snapshot(self, snapshot_ref: ArtifactRef) -> Snapshot:
        """Retrieve and validate a snapshot by its artifact reference."""
        ...

    def get_run(self, run_id: str) -> Run | None:
        """Retrieve run metadata by ID, or None if not found."""
        ...

    def claim_run(
        self,
        run_id: str,
        attempt_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimResult:
        """Conditionally claim a job lease on a pending or queued run."""
        ...

    def set_stage(
        self,
        run_id: str,
        attempt_id: str,
        stage: PublicStage,
    ) -> None:
        """Update public execution stage under active lease ownership."""
        ...

    def append_stage_event(
        self,
        run_id: str,
        attempt_id: str,
        event: StageEvent,
    ) -> None:
        """Append an execution event to the bounded stage_events list (max 40)."""
        ...

    def complete_run(
        self,
        run_id: str,
        attempt_id: str,
        status: RunStatus,
        report_ref: ArtifactRef | None,
        summary: str,
    ) -> None:
        """Conditionally mark a run terminal with validated artifact reference."""
        ...

    def put_report(self, report: Report) -> ArtifactRef:
        """Persist an immutable report object and return its key and SHA-256."""
        ...

    def get_report(self, report_ref: ArtifactRef) -> Report:
        """Retrieve a validated report by its artifact reference."""
        ...

    def save_review(
        self,
        run_id: str,
        actor_sub: str,
        report_id: str,
        decision: ReviewDecision,
        note: str,
    ) -> Review:
        """Record an operator review acknowledgement on a completed report."""
        ...

    def list_public_runs(self) -> list[RunSummary]:
        """List curated public demonstration run summaries."""
        ...

    def get_public_run(self, run_id: str) -> Run | None:
        """Retrieve an explicitly curated public run, or None when absent/private."""
        ...

    def mark_queued(self, run_id: str) -> None:
        """Conditionally transition status to queued if in pending_enqueue / preparing.

        Must NOT regress if the run is already claimed, running, or terminal.
        """
        ...

    def create_report_download_url(
        self,
        report_ref: ArtifactRef,
        expires_in_seconds: int = 300,
    ) -> str:
        """Generate a short-lived download URL (default 5 minutes / 300 seconds) for report JSON."""
        ...


__all__ = [
    # Protocols and adapters
    "ArtifactSignerProtocol",
    "AwsStorage",
    "MemoryStorage",
    "QueueSenderProtocol",
    "StorageProtocol",
    # Exceptions
    "ActiveRunLimitExceededError",
    "ConditionalCheckFailedError",
    "DailyLimitExceededError",
    "IdempotencyConflictError",
    "LimitExceededError",
    "NotFoundError",
    "QueueError",
    "ReportNotReadyError",
    "StateConflictError",
    "StorageError",
    "TemporaryEnqueueError",
    "TemporaryStorageError",
]
