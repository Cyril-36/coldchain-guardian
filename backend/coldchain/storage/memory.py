"""In-memory storage adapter implementing StorageProtocol for offline testing and dev harness."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from coldchain.contracts.enums import GenerationMode, PublicStage, ReviewDecision, RunStatus
from coldchain.contracts.schemas import (
    ArtifactRef,
    ClaimResult,
    QueueMessage,
    Report,
    Review,
    Run,
    RunSummary,
    Snapshot,
    StageEvent,
)


class StorageError(Exception):
    """Base exception for storage errors."""


class IdempotencyConflictError(StorageError, ValueError):
    """Raised when an idempotency key is reused with a different request hash."""


class ConditionalCheckFailedError(StorageError, ValueError):
    """Raised when a conditional check fails (e.g. stale attempt or terminal status)."""


class NotFoundError(StorageError, KeyError):
    """Raised when a requested resource (run, snapshot, report) is not found."""


class MemoryStorage:
    """In-memory implementation of StorageProtocol.

    Suitable for offline tests and local API dev harness.
    Thread/concurrency locks are omitted intentionally for harness simplicity.
    """

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._snapshots: dict[str, Snapshot] = {}
        self._reports: dict[str, Report] = {}
        self._reviews: dict[str, list[Review]] = {}
        # Keyed by (owner_sub, idempotency_key) -> (request_hash, run_id)
        self._idempotency: dict[tuple[str, str], tuple[str, str]] = {}
        # Keyed by run_id -> (attempt_id, lease_expires_at)
        self._leases: dict[str, tuple[str, datetime]] = {}
        self.queue: list[QueueMessage] = []
        self._queue = self.queue

    def create_or_get_run(
        self,
        owner_sub: str,
        idempotency_key: str,
        request_hash: str,
        metadata: dict[str, Any],
    ) -> Run:
        """Create a new run or return an existing run if idempotency key matches.

        If the key exists with a different request_hash, raises IdempotencyConflictError.
        """
        idemp_key = (owner_sub, idempotency_key)
        if idemp_key in self._idempotency:
            stored_hash, existing_run_id = self._idempotency[idemp_key]
            if stored_hash != request_hash:
                raise IdempotencyConflictError(
                    f"Idempotency conflict for key '{idempotency_key}': different request hash"
                )
            return self._runs[existing_run_id]

        now = datetime.now(UTC)
        run_id = str(metadata.get("run_id") or uuid.uuid4())

        status = metadata.get("status", RunStatus.pending_enqueue)
        if isinstance(status, str):
            status = RunStatus(status)

        stage = metadata.get("stage", PublicStage.preparing)
        if isinstance(stage, str):
            stage = PublicStage(stage)

        generation_mode = metadata.get("generation_mode")
        if isinstance(generation_mode, str):
            generation_mode = GenerationMode(generation_mode)

        snapshot_ref = metadata.get("snapshot_ref")
        if isinstance(snapshot_ref, dict):
            snapshot_ref = ArtifactRef(**snapshot_ref)

        report_ref = metadata.get("report_ref")
        if isinstance(report_ref, dict):
            report_ref = ArtifactRef(**report_ref)

        review = metadata.get("review")
        if isinstance(review, dict):
            review = Review(**review)

        created_at = metadata.get("created_at") or now
        updated_at = metadata.get("updated_at") or now

        run = Run(
            run_id=run_id,
            owner_sub=owner_sub,
            status=status,
            stage=stage,
            stage_events=list(metadata.get("stage_events", [])),
            created_at=created_at,
            updated_at=updated_at,
            snapshot_ref=snapshot_ref,
            report_ref=report_ref,
            scenario_id=metadata.get("scenario_id"),
            error=metadata.get("error"),
            review=review,
            generation_mode=generation_mode,
            is_public_demo=bool(metadata.get("is_public_demo", False)),
        )

        self._runs[run_id] = run
        self._idempotency[idemp_key] = (request_hash, run_id)
        return run

    def put_snapshot(self, snapshot: Snapshot) -> ArtifactRef:
        """Store snapshot in-memory and return canonical ArtifactRef with SHA-256."""
        raw = snapshot.model_dump_json(by_alias=True)
        sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        key = f"snapshots/{snapshot.snapshot_id}.json"
        ref = ArtifactRef(key=key, sha256=sha)
        self._snapshots[key] = snapshot
        self._snapshots[snapshot.snapshot_id] = snapshot
        return ref

    def get_snapshot(self, snapshot_ref: ArtifactRef) -> Snapshot:
        """Retrieve snapshot by ArtifactRef. Validates SHA-256 if present on the ref."""
        snapshot = self._snapshots.get(snapshot_ref.key)
        if snapshot is None:
            raise NotFoundError(f"Snapshot not found: {snapshot_ref.key}")
        if snapshot_ref.sha256:
            raw = snapshot.model_dump_json(by_alias=True)
            computed_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            if computed_sha != snapshot_ref.sha256:
                raise ValueError(
                    f"Snapshot SHA-256 mismatch for {snapshot_ref.key}: "
                    f"expected {snapshot_ref.sha256}, got {computed_sha}"
                )
        return snapshot

    def get_run(self, run_id: str) -> Run | None:
        """Retrieve a run by its ID or return None if not found."""
        return self._runs.get(run_id)

    def claim_run(
        self,
        run_id: str,
        attempt_id: str,
        now_iso: str,
        lease_seconds: int,
    ) -> ClaimResult:
        """Attempt to claim a run lease for an execution attempt.

        Rejects if run does not exist, is in terminal status, or has an unexpired lease
        held by another attempt.
        """
        run = self._runs.get(run_id)
        if run is None:
            return ClaimResult(success=False, reason=f"Run {run_id} not found")

        terminal_statuses = {RunStatus.completed, RunStatus.failed, RunStatus.needs_review}
        if run.status in terminal_statuses:
            return ClaimResult(
                success=False,
                reason=f"Run {run_id} is in terminal status: {run.status.value}",
            )

        try:
            now_dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        except Exception as err:
            return ClaimResult(success=False, reason=f"Invalid now_iso timestamp: {err}")

        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=UTC)

        new_expiry = now_dt + timedelta(seconds=lease_seconds)

        if run_id in self._leases:
            active_attempt, expires_at = self._leases[run_id]
            if now_dt < expires_at:
                if active_attempt != attempt_id:
                    return ClaimResult(
                        success=False,
                        reason=(
                            f"Active lease held by attempt {active_attempt} "
                            f"until {expires_at.isoformat()}"
                        ),
                    )
                # Same attempt renewing lease
                self._leases[run_id] = (attempt_id, new_expiry)
                run.updated_at = now_dt
                run.status = RunStatus.running
                return ClaimResult(success=True, reason="Lease extended")

        # Grant lease
        self._leases[run_id] = (attempt_id, new_expiry)
        run.status = RunStatus.running
        run.updated_at = now_dt
        return ClaimResult(success=True, reason="Run claimed successfully")

    def set_stage(self, run_id: str, attempt_id: str, stage: PublicStage) -> None:
        """Update public stage of a running execution.

        Rejects if run is missing, terminal, or attempt is stale.
        """
        run = self._runs.get(run_id)
        if run is None:
            raise NotFoundError(f"Run {run_id} not found")

        terminal_statuses = {RunStatus.completed, RunStatus.failed, RunStatus.needs_review}
        if run.status in terminal_statuses:
            raise ConditionalCheckFailedError(
                f"Run {run_id} is already terminal: {run.status.value}"
            )

        if run_id in self._leases:
            active_attempt, _ = self._leases[run_id]
            if active_attempt != attempt_id:
                raise ConditionalCheckFailedError(
                    f"Stale attempt {attempt_id}: active attempt is {active_attempt}"
                )

        if isinstance(stage, str):
            stage = PublicStage(stage)

        run.stage = stage
        run.updated_at = datetime.now(UTC)

    def append_stage_event(
        self, run_id: str, attempt_id: str, event: StageEvent
    ) -> None:
        """Append a stage event to run's execution trace.

        Bounded to at most 40 events per CONTRACTS.md §101.
        Rejects if run is missing, terminal, or attempt is stale.
        """
        run = self._runs.get(run_id)
        if run is None:
            raise NotFoundError(f"Run {run_id} not found")

        terminal_statuses = {RunStatus.completed, RunStatus.failed, RunStatus.needs_review}
        if run.status in terminal_statuses:
            raise ConditionalCheckFailedError(
                f"Run {run_id} is already terminal: {run.status.value}"
            )

        if run_id in self._leases:
            active_attempt, _ = self._leases[run_id]
            if active_attempt != attempt_id:
                raise ConditionalCheckFailedError(
                    f"Stale attempt {attempt_id}: active attempt is {active_attempt}"
                )

        # CONTRACTS.md §101: stage_events is a bounded list of up to 40 records
        if len(run.stage_events) < 40:
            run.stage_events.append(event)
        run.updated_at = datetime.now(UTC)

    def complete_run(
        self,
        run_id: str,
        attempt_id: str,
        status: RunStatus,
        report_ref: ArtifactRef | None,
        summary: str,
    ) -> None:
        """Mark run completed conditionally.

        Rejects if run is already terminal or attempt is stale.
        """
        run = self._runs.get(run_id)
        if run is None:
            raise NotFoundError(f"Run {run_id} not found")

        terminal_statuses = {RunStatus.completed, RunStatus.failed, RunStatus.needs_review}
        if run.status in terminal_statuses:
            raise ConditionalCheckFailedError(
                f"Run {run_id} is already in terminal status: {run.status.value}"
            )

        if run_id in self._leases:
            active_attempt, _ = self._leases[run_id]
            if active_attempt != attempt_id:
                raise ConditionalCheckFailedError(
                    f"Stale attempt {attempt_id}: active attempt is {active_attempt}"
                )

        if isinstance(status, str):
            status = RunStatus(status)

        run.status = status
        run.report_ref = report_ref
        run.updated_at = datetime.now(UTC)

        if status == RunStatus.failed:
            run.error = summary
            run.stage = PublicStage.failed
        elif status in (RunStatus.completed, RunStatus.needs_review):
            run.stage = PublicStage.ready

    def put_report(self, report: Report) -> ArtifactRef:
        """Store immutable report in-memory and return ArtifactRef with SHA-256."""
        raw = report.model_dump_json(by_alias=True)
        sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        key = f"reports/{report.report_id}.json"
        ref = ArtifactRef(key=key, sha256=sha)
        self._reports[key] = report
        self._reports[report.report_id] = report
        return ref

    def get_report(self, report_ref: ArtifactRef) -> Report:
        """Retrieve report by ArtifactRef. Validates SHA-256 if present on the ref."""
        report = self._reports.get(report_ref.key)
        if report is None:
            raise NotFoundError(f"Report not found: {report_ref.key}")
        if report_ref.sha256:
            raw = report.model_dump_json(by_alias=True)
            computed_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            if computed_sha != report_ref.sha256:
                raise ValueError(
                    f"Report SHA-256 mismatch for {report_ref.key}: "
                    f"expected {report_ref.sha256}, got {computed_sha}"
                )
        return report

    def save_review(
        self,
        run_id: str,
        actor_sub: str,
        report_id: str,
        decision: ReviewDecision,
        note: str,
    ) -> Review:
        """Save operator review.

        Validates that run exists and report_id matches the run's report.
        """
        run = self._runs.get(run_id)
        if run is None:
            raise NotFoundError(f"Run {run_id} not found")

        if run.report_ref is None:
            raise ValueError(f"Run {run_id} has no report; cannot save review")

        expected_report_id = None
        if run.report_ref.key in self._reports:
            expected_report_id = self._reports[run.report_ref.key].report_id
        elif run.report_ref.key.startswith("reports/") and run.report_ref.key.endswith(".json"):
            expected_report_id = run.report_ref.key[len("reports/") : -len(".json")]
        else:
            expected_report_id = run.report_ref.key

        if report_id != expected_report_id:
            raise ValueError(
                f"Report ID mismatch: run has report '{expected_report_id}', "
                f"but review specifies '{report_id}'"
            )

        if isinstance(decision, str):
            decision = ReviewDecision(decision)

        review = Review(
            run_id=run_id,
            actor_sub=actor_sub,
            report_id=report_id,
            decision=decision,
            note=note,
            created_at=datetime.now(UTC),
        )

        run.review = review
        run.updated_at = datetime.now(UTC)

        if run_id not in self._reviews:
            self._reviews[run_id] = []
        self._reviews[run_id].append(review)

        return review

    def list_public_runs(self) -> list[RunSummary]:
        """List curated public demo runs (is_public_demo=True) sorted by created_at desc."""
        summaries: list[RunSummary] = []
        for run in self._runs.values():
            if not run.is_public_demo:
                continue

            outcome = None
            if run.report_ref is not None:
                report = self._reports.get(run.report_ref.key)
                if report is not None:
                    outcome = report.outcome

            summaries.append(
                RunSummary(
                    run_id=run.run_id,
                    status=run.status,
                    stage=run.stage,
                    scenario_id=run.scenario_id,
                    created_at=run.created_at,
                    updated_at=run.updated_at,
                    outcome=outcome,
                    is_public_demo=True,
                )
            )

        summaries.sort(key=lambda s: s.created_at, reverse=True)
        return summaries

    def enqueue_run(
        self, run_id: str, snapshot_id: str, schema_version: str
    ) -> None:
        """Enqueue run message into the internal queue list for consumption."""
        msg = QueueMessage(
            run_id=run_id,
            snapshot_id=snapshot_id,
            schema_version=schema_version,
        )
        self.queue.append(msg)

        if run_id in self._runs:
            run = self._runs[run_id]
            if run.status == RunStatus.pending_enqueue:
                run.status = RunStatus.queued
                run.updated_at = datetime.now(UTC)
