"""Thread-safe in-memory storage with production-like conditional semantics."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from threading import RLock
from typing import Any
from uuid import UUID, uuid4, uuid5

from coldchain.simulator.validation import normalize_snapshot

from .exceptions import (
    ActiveRunConflict,
    ArtifactDigestMismatch,
    ArtifactNotFound,
    ConditionalWriteConflict,
    DailyLimitExceeded,
    IdempotencyConflict,
    ImmutableArtifactConflict,
    LeaseConflict,
    ReviewConflict,
    RunNotFound,
)
from .models import (
    TERMINAL_STATUSES,
    ActiveRunLease,
    ArtifactRef,
    ClaimResult,
    ReviewRecord,
    RunRecord,
    RunStage,
    RunStatus,
    RunSummary,
)
from .serialization import (
    ArtifactValidator,
    canonical_json_bytes,
    copy_mapping,
    deserialize_verified,
    sha256_hex,
)

_ID_NAMESPACE = UUID("42568e65-3890-4d6f-b710-ad9318d4ce8a")
_STAGE_ORDER = {
    "preparing": 0,
    "detecting": 1,
    "collecting_evidence": 2,
    "comparing_hypotheses": 3,
    "verifying": 4,
    "ready": 5,
    "failed": 5,
}


class MemoryStorage:
    """Offline adapter that copies values and guards every atomic operation."""

    def __init__(
        self,
        *,
        snapshot_validator: ArtifactValidator = normalize_snapshot,
        report_validator: ArtifactValidator = copy_mapping,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._snapshot_validator = snapshot_validator
        self._report_validator = report_validator
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = RLock()
        self._runs: dict[str, RunRecord] = {}
        self._idempotency: dict[tuple[str, str], tuple[str, str, datetime]] = {}
        self._artifacts: dict[str, bytes] = {}
        self._reviews: dict[str, list[ReviewRecord]] = {}
        self._daily_counts: dict[date, int] = {}
        self._active_runs: dict[str, ActiveRunLease] = {}
        self._public_runs: dict[str, RunSummary] = {}

    @staticmethod
    def _key_hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _copy_run(run: RunRecord) -> RunRecord:
        return deepcopy(run)

    def _require_run(self, run_id: str) -> RunRecord:
        run = self._runs.get(run_id)
        if run is None:
            raise RunNotFound("run does not exist")
        return run

    def create_or_get_run(
        self,
        owner_sub: str,
        idempotency_key: str,
        request_hash: str,
        metadata: Mapping[str, Any],
    ) -> RunRecord:
        scope = (owner_sub, self._key_hash(idempotency_key))
        with self._lock:
            existing = self._idempotency.get(scope)
            if existing is not None:
                existing_hash, run_id, expires_at = existing
                if expires_at > self._clock():
                    if existing_hash != request_hash:
                        raise IdempotencyConflict(
                            "idempotency key was already used with different request content"
                        )
                    return self._copy_run(self._require_run(run_id))
                del self._idempotency[scope]

            run_id = str(metadata.get("run_id") or self._id_factory())
            if run_id in self._runs:
                raise ConditionalWriteConflict("run_id already exists")
            run = RunRecord(
                run_id=run_id,
                owner_sub=owner_sub,
                request_hash=request_hash,
                metadata=deepcopy(dict(metadata)),
            )
            self._runs[run_id] = run
            self._idempotency[scope] = (
                request_hash,
                run_id,
                self._clock() + timedelta(hours=24),
            )
            return self._copy_run(run)

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            run = self._runs.get(run_id)
            return None if run is None else self._copy_run(run)

    def mark_queued(self, run_id: str) -> bool:
        """Conditionally apply the API's queued update without regressing state."""

        with self._lock:
            run = self._require_run(run_id)
            if run.status == "pending_enqueue":
                run.status = "queued"
                return True
            return run.status == "queued"

    def claim_run(
        self,
        run_id: str,
        attempt_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimResult:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self._lock:
            run = self._require_run(run_id)
            if run.status in TERMINAL_STATUSES:
                return ClaimResult(False, self._copy_run(run), "terminal")
            if run.status == "running" and run.attempt_id == attempt_id:
                run.lease_expires_at = now + timedelta(seconds=lease_seconds)
                return ClaimResult(True, self._copy_run(run), "same_attempt")
            if (
                run.status == "running"
                and run.lease_expires_at is not None
                and run.lease_expires_at > now
            ):
                raise LeaseConflict("run has an active worker lease")
            run.status = "running"
            run.attempt_id = attempt_id
            run.lease_expires_at = now + timedelta(seconds=lease_seconds)
            return ClaimResult(True, self._copy_run(run))

    def _require_current_attempt(self, run: RunRecord, attempt_id: str) -> None:
        if run.status != "running" or run.attempt_id != attempt_id:
            raise ConditionalWriteConflict("worker attempt is not current")

    def set_stage(self, run_id: str, attempt_id: str, stage: RunStage) -> None:
        with self._lock:
            run = self._require_run(run_id)
            self._require_current_attempt(run, attempt_id)
            if _STAGE_ORDER[stage] < _STAGE_ORDER[run.stage]:
                raise ConditionalWriteConflict("stage cannot regress")
            run.stage = stage

    def append_stage_event(
        self,
        run_id: str,
        attempt_id: str,
        event: Mapping[str, Any],
    ) -> None:
        with self._lock:
            run = self._require_run(run_id)
            self._require_current_attempt(run, attempt_id)
            if len(run.stage_events) >= 40:
                raise ConditionalWriteConflict("stage event limit reached")
            run.stage_events.append(deepcopy(dict(event)))

    def complete_run(
        self,
        run_id: str,
        attempt_id: str,
        status: RunStatus,
        report_ref: ArtifactRef,
        summary: Mapping[str, Any],
    ) -> None:
        if status not in TERMINAL_STATUSES:
            raise ValueError("completion status must be terminal")
        with self._lock:
            run = self._require_run(run_id)
            if run.status in TERMINAL_STATUSES:
                if (
                    run.attempt_id == attempt_id
                    and run.status == status
                    and run.report_ref == report_ref
                    and run.summary == dict(summary)
                ):
                    return
                raise ConditionalWriteConflict("run already has a different terminal result")
            self._require_current_attempt(run, attempt_id)
            report_payload = self._artifacts.get(report_ref.key)
            if report_payload is None:
                raise ArtifactNotFound("report artifact must exist before completion")
            if sha256_hex(report_payload) != report_ref.sha256:
                raise ArtifactDigestMismatch("report digest does not match stored bytes")
            run.status = status
            run.stage = "failed" if status == "failed" else "ready"
            run.report_ref = report_ref
            run.summary = deepcopy(dict(summary))

    def _put_artifact(
        self,
        artifact_id: str,
        prefix: str,
        value: Mapping[str, Any],
        validator: ArtifactValidator,
    ) -> ArtifactRef:
        UUID(artifact_id)
        validated = validator(value)
        payload = canonical_json_bytes(validated)
        key = f"{prefix}/{artifact_id}.json"
        reference = ArtifactRef(key=key, sha256=sha256_hex(payload), artifact_id=artifact_id)
        with self._lock:
            existing = self._artifacts.get(key)
            if existing is not None and existing != payload:
                raise ImmutableArtifactConflict("artifact key already stores different bytes")
            self._artifacts.setdefault(key, payload)
        return reference

    def _get_artifact(
        self,
        reference: ArtifactRef,
        prefix: str,
        validator: ArtifactValidator,
    ) -> dict[str, Any]:
        UUID(reference.artifact_id)
        if reference.key != f"{prefix}/{reference.artifact_id}.json":
            raise ValueError("artifact reference key is not canonical")
        with self._lock:
            payload = self._artifacts.get(reference.key)
            if payload is None:
                raise ArtifactNotFound("artifact does not exist")
            copied = bytes(payload)
        return deserialize_verified(copied, reference.sha256, validator)

    def put_snapshot(self, snapshot: Mapping[str, Any]) -> ArtifactRef:
        return self._put_artifact(
            str(snapshot["snapshot_id"]), "snapshots", snapshot, self._snapshot_validator
        )

    def get_snapshot(self, snapshot_ref: ArtifactRef) -> dict[str, Any]:
        return self._get_artifact(snapshot_ref, "snapshots", self._snapshot_validator)

    def put_report(self, report: Mapping[str, Any]) -> ArtifactRef:
        return self._put_artifact(
            str(report["report_id"]), "reports", report, self._report_validator
        )

    def get_report(self, report_ref: ArtifactRef) -> dict[str, Any]:
        return self._get_artifact(report_ref, "reports", self._report_validator)

    def save_review(
        self,
        run_id: str,
        actor_sub: str,
        report_id: str,
        decision: str,
        note: str,
    ) -> ReviewRecord:
        if len(note) > 1_000:
            raise ValueError("review note exceeds 1000 characters")
        if decision not in {"acknowledged", "request_more_evidence"}:
            raise ValueError("review decision is invalid")
        with self._lock:
            run = self._require_run(run_id)
            if run.report_ref is None or run.report_ref.artifact_id != report_id:
                raise ReviewConflict("review must reference the run's current report")
            review_number = len(self._reviews.get(run_id, []))
            review_id = str(uuid5(_ID_NAMESPACE, f"{run_id}|review|{review_number}"))
            review = ReviewRecord(
                review_id=review_id,
                run_id=run_id,
                actor_sub=actor_sub,
                report_id=report_id,
                decision=decision,
                note=note,
            )
            self._reviews.setdefault(run_id, []).append(review)
            return review

    def list_reviews(self, run_id: str) -> list[ReviewRecord]:
        with self._lock:
            self._require_run(run_id)
            return deepcopy(self._reviews.get(run_id, []))

    def set_public_demo(self, run_id: str, summary: Mapping[str, Any]) -> None:
        with self._lock:
            run = self._require_run(run_id)
            run.is_public_demo = True
            self._public_runs[run_id] = RunSummary(run_id, deepcopy(dict(summary)))

    def list_public_runs(self) -> list[RunSummary]:
        with self._lock:
            return deepcopy(list(self._public_runs.values())[:5])

    def get_public_run(self, run_id: str) -> RunSummary | None:
        with self._lock:
            return deepcopy(self._public_runs.get(run_id))

    def claim_daily_run(self, utc_date: date, limit: int = 50) -> int:
        with self._lock:
            current = self._daily_counts.get(utc_date, 0)
            if current >= limit:
                raise DailyLimitExceeded("UTC daily run limit reached")
            current += 1
            self._daily_counts[utc_date] = current
            return current

    def claim_active_run(
        self,
        owner_sub: str,
        run_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ActiveRunLease:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self._lock:
            existing = self._active_runs.get(owner_sub)
            if existing is not None and existing.run_id == run_id:
                lease = ActiveRunLease(owner_sub, run_id, now + timedelta(seconds=lease_seconds))
                self._active_runs[owner_sub] = lease
                return lease
            if existing is not None and existing.expires_at > now:
                raise ActiveRunConflict("operator already has an active run")
            lease = ActiveRunLease(owner_sub, run_id, now + timedelta(seconds=lease_seconds))
            self._active_runs[owner_sub] = lease
            return lease

    def release_active_run(self, owner_sub: str, run_id: str) -> bool:
        with self._lock:
            existing = self._active_runs.get(owner_sub)
            if existing is None or existing.run_id != run_id:
                return False
            del self._active_runs[owner_sub]
            return True

    def _corrupt_artifact_for_test(self, key: str, payload: bytes) -> None:
        """Test seam for proving that digest verification rejects changed bytes."""

        with self._lock:
            if key not in self._artifacts:
                raise ArtifactNotFound("artifact does not exist")
            self._artifacts[key] = payload
