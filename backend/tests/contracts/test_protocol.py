"""Test suite verifying StorageProtocol typing and storage exceptions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

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
from coldchain.storage import (
    ActiveRunLimitExceededError,
    ArtifactSignerProtocol,
    ConditionalCheckFailedError,
    DailyLimitExceededError,
    IdempotencyConflictError,
    LimitExceededError,
    NotFoundError,
    QueueError,
    QueueSenderProtocol,
    ReportNotReadyError,
    StateConflictError,
    StorageError,
    StorageProtocol,
    TemporaryEnqueueError,
    TemporaryStorageError,
)


class DummyValidStorage:
    """Mock class providing exact signatures required by StorageProtocol."""

    def create_or_get_run(
        self,
        owner_sub: str,
        idempotency_key: str,
        request_hash: str,
        metadata: dict[str, Any],
    ) -> Run:
        raise NotImplementedError

    def attach_snapshot(
        self,
        run_id: str,
        snapshot_ref: ArtifactRef,
    ) -> None:
        raise NotImplementedError

    def put_snapshot(self, snapshot: Snapshot) -> ArtifactRef:
        raise NotImplementedError

    def get_snapshot(self, snapshot_ref: ArtifactRef) -> Snapshot:
        raise NotImplementedError

    def get_run(self, run_id: str) -> Run | None:
        raise NotImplementedError

    def claim_run(
        self,
        run_id: str,
        attempt_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimResult:
        raise NotImplementedError

    def set_stage(
        self,
        run_id: str,
        attempt_id: str,
        stage: PublicStage,
    ) -> None:
        raise NotImplementedError

    def append_stage_event(
        self,
        run_id: str,
        attempt_id: str,
        event: StageEvent,
    ) -> None:
        raise NotImplementedError

    def complete_run(
        self,
        run_id: str,
        attempt_id: str,
        status: RunStatus,
        report_ref: ArtifactRef | None,
        summary: str,
    ) -> None:
        raise NotImplementedError

    def put_report(self, report: Report) -> ArtifactRef:
        raise NotImplementedError

    def get_report(self, report_ref: ArtifactRef) -> Report:
        raise NotImplementedError

    def save_review(
        self,
        run_id: str,
        actor_sub: str,
        report_id: str,
        decision: ReviewDecision,
        note: str,
    ) -> Review:
        raise NotImplementedError

    def list_public_runs(self) -> list[RunSummary]:
        raise NotImplementedError

    def get_public_run(self, run_id: str) -> Run | None:
        raise NotImplementedError

    def mark_queued(self, run_id: str) -> None:
        raise NotImplementedError

    def create_report_download_url(
        self,
        report_ref: ArtifactRef,
        expires_in_seconds: int = 300,
    ) -> str:
        raise NotImplementedError


class DummyQueueSender:
    """Mock class providing exact signatures required by QueueSenderProtocol."""

    def send_run(
        self,
        run_id: str,
        snapshot_id: str,
        schema_version: str = "1.0",
    ) -> None:
        raise NotImplementedError

    def enqueue_run(
        self,
        run_id: str,
        snapshot_id: str,
        schema_version: str = "1.0",
    ) -> None:
        raise NotImplementedError


class DummyArtifactSigner:
    """Mock class providing exact signatures required by ArtifactSignerProtocol."""

    def create_report_download_url(
        self,
        report_ref: ArtifactRef,
        expires_in_seconds: int = 300,
    ) -> str:
        raise NotImplementedError


class DummyIncompleteStorage:
    """Mock class missing multiple required methods."""

    def get_run(self, run_id: str) -> Run | None:
        return None


def test_storage_protocol_runtime_checkable() -> None:
    storage = DummyValidStorage()
    assert isinstance(storage, StorageProtocol)


def test_queue_sender_protocol_runtime_checkable() -> None:
    sender = DummyQueueSender()
    assert isinstance(sender, QueueSenderProtocol)


def test_artifact_signer_protocol_runtime_checkable() -> None:
    signer = DummyArtifactSigner()
    assert isinstance(signer, ArtifactSignerProtocol)
    # Combined storage also satisfies ArtifactSignerProtocol
    storage = DummyValidStorage()
    assert isinstance(storage, ArtifactSignerProtocol)


def test_storage_protocol_rejects_incomplete_class() -> None:
    incomplete = DummyIncompleteStorage()
    assert not isinstance(incomplete, StorageProtocol)


def test_storage_exception_hierarchy() -> None:
    # 404
    assert issubclass(NotFoundError, StorageError)
    assert issubclass(NotFoundError, KeyError)
    assert NotFoundError.status_code == 404
    assert not NotFoundError.retryable

    # 409
    assert issubclass(StateConflictError, StorageError)
    assert issubclass(StateConflictError, ValueError)
    assert StateConflictError.status_code == 409
    assert not StateConflictError.retryable

    assert issubclass(IdempotencyConflictError, StateConflictError)
    assert issubclass(ConditionalCheckFailedError, StateConflictError)
    assert issubclass(ReportNotReadyError, StateConflictError)

    # 429
    assert issubclass(LimitExceededError, StorageError)
    assert LimitExceededError.status_code == 429
    assert not LimitExceededError.retryable

    assert issubclass(DailyLimitExceededError, LimitExceededError)
    assert issubclass(ActiveRunLimitExceededError, LimitExceededError)

    # 503
    assert issubclass(TemporaryStorageError, StorageError)
    assert TemporaryStorageError.status_code == 503
    assert TemporaryStorageError.retryable

    assert issubclass(QueueError, StorageError)
    assert QueueError.status_code == 503
    assert QueueError.retryable

    assert issubclass(TemporaryEnqueueError, QueueError)
    assert issubclass(TemporaryEnqueueError, TemporaryStorageError)
    assert TemporaryEnqueueError.status_code == 503
    assert TemporaryEnqueueError.retryable

    err = IdempotencyConflictError("conflict")
    with pytest.raises(StorageError):
        raise err
