"""Storage protocol — interface that Harshith implements with AWS adapters.

Leader (Cyril) owns the semantics; Harshith owns adapter code.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from coldchain.contracts.enums import PublicStage, RunStatus
from coldchain.contracts.schemas import (
    ArtifactRef,
    ClaimResult,
    Report,
    Review,
    ReviewDecision,
    Run,
    RunSummary,
    Snapshot,
    StageEvent,
)

from .memory import (
    ConditionalCheckFailedError,
    IdempotencyConflictError,
    MemoryStorage,
    NotFoundError,
    StorageError,
)


@runtime_checkable
class StorageProtocol(Protocol):
    """Abstract storage operations per CONTRACTS.md §Storage interface."""

    def create_or_get_run(
        self,
        owner_sub: str,
        idempotency_key: str,
        request_hash: str,
        metadata: dict,
    ) -> Run: ...

    def put_snapshot(self, snapshot: Snapshot) -> ArtifactRef: ...

    def get_snapshot(self, snapshot_ref: ArtifactRef) -> Snapshot: ...

    def get_run(self, run_id: str) -> Run | None: ...

    def claim_run(
        self,
        run_id: str,
        attempt_id: str,
        now_iso: str,
        lease_seconds: int,
    ) -> ClaimResult: ...

    def set_stage(self, run_id: str, attempt_id: str, stage: PublicStage) -> None: ...

    def append_stage_event(
        self, run_id: str, attempt_id: str, event: StageEvent
    ) -> None: ...

    def complete_run(
        self,
        run_id: str,
        attempt_id: str,
        status: RunStatus,
        report_ref: ArtifactRef | None,
        summary: str,
    ) -> None: ...

    def put_report(self, report: Report) -> ArtifactRef: ...

    def get_report(self, report_ref: ArtifactRef) -> Report: ...

    def save_review(
        self,
        run_id: str,
        actor_sub: str,
        report_id: str,
        decision: ReviewDecision,
        note: str,
    ) -> Review: ...

    def list_public_runs(self) -> list[RunSummary]: ...

    def enqueue_run(
        self, run_id: str, snapshot_id: str, schema_version: str
    ) -> None: ...


__all__ = [
    "ConditionalCheckFailedError",
    "IdempotencyConflictError",
    "MemoryStorage",
    "NotFoundError",
    "StorageError",
    "StorageProtocol",
]
