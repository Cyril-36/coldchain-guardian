"""Thread-safe in-memory implementation of the canonical storage protocol."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from threading import RLock
from typing import Any
from uuid import UUID, uuid4, uuid5

from coldchain.contracts.enums import PublicStage, ReviewDecision, RunStatus
from coldchain.contracts.schemas import (
    ArtifactRef,
    ClaimResult,
    ErrorDetail,
    Report,
    ReportSummary,
    Review,
    Run,
    RunSummary,
    Snapshot,
    StageEvent,
)

from .exceptions import (
    ActiveRunLimitExceededError,
    ConditionalCheckFailedError,
    DailyLimitExceededError,
    IdempotencyConflictError,
    NotFoundError,
    StateConflictError,
)
from .serialization import canonical_json_bytes, deserialize_verified, sha256_hex

_ID_NAMESPACE = UUID("42568e65-3890-4d6f-b710-ad9318d4ce8a")
_TERMINAL = {RunStatus.completed, RunStatus.needs_review, RunStatus.failed}
_STAGE_ORDER = {
    PublicStage.preparing: 0,
    PublicStage.detecting: 1,
    PublicStage.collecting_evidence: 2,
    PublicStage.comparing_hypotheses: 3,
    PublicStage.verifying: 4,
    PublicStage.ready: 5,
    PublicStage.failed: 5,
}


class MemoryStorage:
    """Offline adapter with the same atomic and immutable semantics as AWS storage."""

    def __init__(
        self,
        *,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        daily_run_limit: int = 50,
        active_run_lease_seconds: int = 300,
    ) -> None:
        if daily_run_limit <= 0 or active_run_lease_seconds <= 0:
            raise ValueError("storage limits must be positive")
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._daily_run_limit = daily_run_limit
        self._active_run_lease_seconds = active_run_lease_seconds
        self._lock = RLock()
        self._runs: dict[str, Run] = {}
        self._idempotency: dict[tuple[str, str], tuple[str, str, datetime]] = {}
        self._artifacts: dict[str, bytes] = {}
        self._reviews: dict[str, list[Review]] = {}
        self._daily_counts: dict[date, int] = {}
        self._active_runs: dict[str, tuple[str, datetime]] = {}
        self._public_run_ids: list[str] = []

    @staticmethod
    def _key_hash(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _copy_run(run: Run) -> Run:
        return run.model_copy(deep=True)

    def _require_run(self, run_id: str) -> Run:
        run = self._runs.get(run_id)
        if run is None:
            raise NotFoundError("run does not exist")
        return run

    def create_or_get_run(
        self,
        owner_sub: str,
        idempotency_key: str,
        request_hash: str,
        metadata: dict[str, Any],
    ) -> Run:
        scope = (owner_sub, self._key_hash(idempotency_key))
        with self._lock:
            now = self._clock()
            existing = self._idempotency.get(scope)
            if existing is not None:
                existing_hash, run_id, expires_at = existing
                if expires_at > now:
                    if existing_hash != request_hash:
                        raise IdempotencyConflictError(
                            "idempotency key was reused with different request content"
                        )
                    return self._copy_run(self._require_run(run_id))
                del self._idempotency[scope]

            utc_day = now.astimezone(UTC).date()
            count = self._daily_counts.get(utc_day, 0)
            if count >= self._daily_run_limit:
                raise DailyLimitExceededError("UTC daily run limit reached")
            active = self._active_runs.get(owner_sub)
            if active is not None and active[1] > now:
                raise ActiveRunLimitExceededError("operator already has an active run")

            run_id = str(metadata.get("run_id") or self._id_factory())
            snapshot_id = str(metadata.get("snapshot_id") or self._id_factory())
            shipment_id = str(metadata.get("shipment_id") or self._id_factory())
            if run_id in self._runs:
                raise ConditionalCheckFailedError("run_id already exists")
            run = Run(
                run_id=run_id,
                owner_sub=owner_sub,
                status=RunStatus.pending_enqueue,
                stage=PublicStage.preparing,
                created_at=metadata.get("created_at", now),
                shipment_id=shipment_id,
                snapshot_id=snapshot_id,
                scenario_id=metadata.get("scenario_id"),
                seed=metadata.get("seed"),
                base_timestamp=metadata.get("base_timestamp"),
                label=metadata.get("label"),
                is_public_demo=bool(metadata.get("is_public_demo", False)),
            )
            self._runs[run_id] = run
            self._idempotency[scope] = (request_hash, run_id, now + timedelta(hours=24))
            self._daily_counts[utc_day] = count + 1
            self._active_runs[owner_sub] = (
                run_id,
                now + timedelta(seconds=self._active_run_lease_seconds),
            )
            return self._copy_run(run)

    def get_run(self, run_id: str) -> Run | None:
        with self._lock:
            run = self._runs.get(run_id)
            return None if run is None else self._copy_run(run)

    def mark_queued(self, run_id: str) -> None:
        with self._lock:
            run = self._require_run(run_id)
            if run.status == RunStatus.pending_enqueue:
                if run.snapshot_ref is None:
                    raise ConditionalCheckFailedError(
                        "run cannot be queued before its snapshot is durable"
                    )
                run.status = RunStatus.queued

    def claim_run(
        self, run_id: str, attempt_id: str, now: datetime, lease_seconds: int
    ) -> ClaimResult:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self._lock:
            run = self._require_run(run_id)
            if run.status in _TERMINAL:
                return ClaimResult(success=False, reason="terminal")
            if run.status == RunStatus.running and run.attempt_id == attempt_id:
                run.lease_expires_at = now + timedelta(seconds=lease_seconds)
                return ClaimResult(success=True, reason="same_attempt")
            if (
                run.status == RunStatus.running
                and run.lease_expires_at is not None
                and run.lease_expires_at > now
            ):
                return ClaimResult(success=False, reason="active_lease")
            run.status = RunStatus.running
            run.attempt_id = attempt_id
            run.lease_expires_at = now + timedelta(seconds=lease_seconds)
            return ClaimResult(success=True, reason="claimed")

    @staticmethod
    def _require_current_attempt(run: Run, attempt_id: str) -> None:
        if run.status != RunStatus.running or run.attempt_id != attempt_id:
            raise ConditionalCheckFailedError("worker attempt is not current")

    def set_stage(self, run_id: str, attempt_id: str, stage: PublicStage) -> None:
        stage = PublicStage(stage)
        with self._lock:
            run = self._require_run(run_id)
            self._require_current_attempt(run, attempt_id)
            if _STAGE_ORDER[stage] < _STAGE_ORDER[run.stage]:
                raise ConditionalCheckFailedError("stage cannot regress")
            run.stage = stage

    def append_stage_event(
        self, run_id: str, attempt_id: str, event: StageEvent
    ) -> None:
        event = StageEvent.model_validate(event)
        with self._lock:
            run = self._require_run(run_id)
            self._require_current_attempt(run, attempt_id)
            if len(run.stage_events) >= 40:
                raise ConditionalCheckFailedError("stage event limit reached")
            run.stage_events.append(event.model_copy(deep=True))

    def complete_run(
        self,
        run_id: str,
        attempt_id: str,
        status: RunStatus,
        report_ref: ArtifactRef | None,
        summary: str,
    ) -> None:
        status = RunStatus(status)
        if status not in _TERMINAL:
            raise ValueError("completion status must be terminal")
        with self._lock:
            run = self._require_run(run_id)
            if run.status in _TERMINAL:
                if (
                    run.status == status
                    and run.attempt_id == attempt_id
                    and run.report_ref == report_ref
                ):
                    return
                raise ConditionalCheckFailedError("run already has a different terminal result")
            self._require_current_attempt(run, attempt_id)
            report: Report | None = None
            if report_ref is not None:
                report = self.get_report(report_ref)
            elif status != RunStatus.failed:
                raise ConditionalCheckFailedError("successful completion requires a report")
            run.status = status
            run.stage = PublicStage.failed if status == RunStatus.failed else PublicStage.ready
            run.completed_at = self._clock()
            run.report_ref = report_ref
            if report is not None:
                run.report_id = report.report_id
                run.generation_mode = report.generation_mode
                run.report_summary = ReportSummary(
                    outcome=report.outcome,
                    primary_hypothesis=report.primary_hypothesis,
                    review_required=report.review_required,
                )
            if status == RunStatus.failed:
                run.error = ErrorDetail(
                    code="worker_failed",
                    message=summary,
                    request_id=run.run_id,
                    retryable=False,
                )
            if run.owner_sub and self._active_runs.get(run.owner_sub, (None,))[0] == run_id:
                del self._active_runs[run.owner_sub]

    def _put_artifact(self, artifact_id: str, prefix: str, value: Snapshot | Report) -> ArtifactRef:
        UUID(artifact_id)
        payload = canonical_json_bytes(value)
        key = f"{prefix}/{artifact_id}.json"
        reference = ArtifactRef(key=key, sha256=sha256_hex(payload))
        with self._lock:
            existing = self._artifacts.get(key)
            if existing is not None and existing != payload:
                raise StateConflictError("artifact key already stores different bytes")
            self._artifacts.setdefault(key, payload)
        return reference

    def _get_artifact(
        self,
        reference: ArtifactRef,
        prefix: str,
        validator: Callable[[Any], Snapshot | Report],
    ) -> Snapshot | Report:
        expected_prefix = f"{prefix}/"
        if not reference.key.startswith(expected_prefix) or not reference.key.endswith(".json"):
            raise ValueError("artifact reference key is not canonical")
        UUID(reference.key[len(expected_prefix) : -5])
        with self._lock:
            payload = self._artifacts.get(reference.key)
            if payload is None:
                raise NotFoundError("artifact does not exist")
        return deserialize_verified(bytes(payload), reference.sha256, validator)

    def put_snapshot(self, snapshot: Snapshot) -> ArtifactRef:
        value = Snapshot.model_validate(snapshot)
        return self._put_artifact(value.snapshot_id, "snapshots", value)

    def get_snapshot(self, snapshot_ref: ArtifactRef) -> Snapshot:
        result = self._get_artifact(snapshot_ref, "snapshots", Snapshot.model_validate)
        assert isinstance(result, Snapshot)
        return result

    def attach_snapshot(self, run_id: str, snapshot_ref: ArtifactRef) -> None:
        snapshot = self.get_snapshot(snapshot_ref)
        with self._lock:
            run = self._require_run(run_id)
            if snapshot.snapshot_id != run.snapshot_id:
                raise ConditionalCheckFailedError("snapshot does not match reserved snapshot_id")
            if run.snapshot_ref is not None and run.snapshot_ref != snapshot_ref:
                raise ConditionalCheckFailedError("run already references a different snapshot")
            if run.status != RunStatus.pending_enqueue and run.snapshot_ref is None:
                raise ConditionalCheckFailedError("snapshot can only attach before enqueue")
            run.snapshot_ref = snapshot_ref

    def put_report(self, report: Report) -> ArtifactRef:
        value = Report.model_validate(report)
        return self._put_artifact(value.report_id, "reports", value)

    def get_report(self, report_ref: ArtifactRef) -> Report:
        result = self._get_artifact(report_ref, "reports", Report.model_validate)
        assert isinstance(result, Report)
        return result

    def save_review(
        self,
        run_id: str,
        actor_sub: str,
        report_id: str,
        decision: ReviewDecision,
        note: str,
    ) -> Review:
        with self._lock:
            run = self._require_run(run_id)
            if run.report_id != report_id:
                raise StateConflictError("review must reference the run's current report")
            review_number = len(self._reviews.get(run_id, []))
            review = Review(
                review_id=str(uuid5(_ID_NAMESPACE, f"{run_id}|review|{review_number}")),
                run_id=run_id,
                actor_sub=actor_sub,
                report_id=report_id,
                decision=decision,
                note=note,
                reviewed_at=self._clock(),
            )
            self._reviews.setdefault(run_id, []).append(review)
            run.review = review
            return review.model_copy(deep=True)

    def list_reviews(self, run_id: str) -> list[Review]:
        with self._lock:
            self._require_run(run_id)
            return [item.model_copy(deep=True) for item in self._reviews.get(run_id, [])]

    def set_public_demo(self, run_id: str, summary: Mapping[str, Any]) -> None:
        with self._lock:
            run = self._require_run(run_id)
            run.is_public_demo = True
            run.label = str(summary.get("label")) if summary.get("label") else run.label
            if run_id not in self._public_run_ids:
                self._public_run_ids.append(run_id)

    @staticmethod
    def _summary(run: Run) -> RunSummary:
        return RunSummary(
            run_id=run.run_id,
            status=run.status,
            stage=run.stage,
            created_at=run.created_at,
            completed_at=run.completed_at,
            report_id=run.report_id,
            review=run.review,
            generation_mode=run.generation_mode,
            is_public_demo=run.is_public_demo,
            label=run.label,
        )

    def list_public_runs(self) -> list[RunSummary]:
        with self._lock:
            return [self._summary(self._runs[run_id]) for run_id in self._public_run_ids[:5]]

    def get_public_run(self, run_id: str) -> Run | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or not run.is_public_demo:
                return None
            return self._copy_run(run)

    def create_report_download_url(
        self, report_ref: ArtifactRef, expires_in_seconds: int = 300
    ) -> str:
        if not 1 <= expires_in_seconds <= 300:
            raise ValueError("report URL expiry must be between 1 and 300 seconds")
        self.get_report(report_ref)
        return f"memory://{report_ref.key}?expires_in={expires_in_seconds}"

    def claim_daily_run(self, utc_date: date, limit: int = 50) -> int:
        with self._lock:
            count = self._daily_counts.get(utc_date, 0)
            if count >= limit:
                raise DailyLimitExceededError("UTC daily run limit reached")
            self._daily_counts[utc_date] = count + 1
            return count + 1

    def claim_active_run(
        self, owner_sub: str, run_id: str, now: datetime, lease_seconds: int
    ) -> tuple[str, datetime]:
        with self._lock:
            existing = self._active_runs.get(owner_sub)
            if existing is not None and existing[0] != run_id and existing[1] > now:
                raise ActiveRunLimitExceededError("operator already has an active run")
            lease = (run_id, now + timedelta(seconds=lease_seconds))
            self._active_runs[owner_sub] = lease
            return lease

    def release_active_run(self, owner_sub: str, run_id: str) -> bool:
        with self._lock:
            existing = self._active_runs.get(owner_sub)
            if existing is None or existing[0] != run_id:
                return False
            del self._active_runs[owner_sub]
            return True

    def _corrupt_artifact_for_test(self, key: str, payload: bytes) -> None:
        with self._lock:
            if key not in self._artifacts:
                raise NotFoundError("artifact does not exist")
            self._artifacts[key] = payload
