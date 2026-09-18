"""Business operations behind the HTTP transport."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from coldchain.contracts import (
    CreateRunRequest,
    Report,
    Review,
    ReviewRequest,
    Run,
    RunResponse,
    RunStatus,
    Snapshot,
)
from coldchain.simulator import SCENARIO_IDS, generate_snapshot
from coldchain.storage import (
    NotFoundError,
    QueueError,
    QueueSenderProtocol,
    ReportNotReadyError,
    StorageError,
    StorageProtocol,
    TemporaryEnqueueError,
)

SCENARIO_CATALOGUE: tuple[dict[str, str], ...] = (
    {"scenario_id": "normal_control", "label": "Normal control"},
    {"scenario_id": "door_exposure", "label": "Door exposure"},
    {"scenario_id": "refrigeration_problem", "label": "Refrigeration problem"},
    {"scenario_id": "sensor_disagreement", "label": "Sensor disagreement"},
    {"scenario_id": "ambiguous_incident", "label": "Ambiguous incident"},
)


class PublicRunStorage(StorageProtocol, Protocol):
    """API-required extension implemented by both canonical storage adapters."""

    def get_public_run(self, run_id: str) -> Run | None: ...


def _request_hash(request: CreateRunRequest) -> str:
    payload = json.dumps(
        request.model_dump(mode="json"),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _require_uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except (TypeError, ValueError) as error:
        raise NotFoundError("run does not exist") from error
    if str(parsed) != value:
        raise NotFoundError("run does not exist")
    return value


class ApiService:
    """Storage-backed API use cases, independent of API Gateway event parsing."""

    def __init__(
        self,
        storage: PublicRunStorage,
        queue: QueueSenderProtocol,
        *,
        clock: Callable[[], datetime] | None = None,
        seed_factory: Callable[[], int] | None = None,
    ) -> None:
        self.storage = storage
        self.queue = queue
        self._clock = clock or (lambda: datetime.now(UTC))
        self._seed_factory = seed_factory or (lambda: secrets.randbits(63))

    @staticmethod
    def scenarios() -> list[dict[str, str]]:
        return [dict(item) for item in SCENARIO_CATALOGUE]

    def create_run(
        self,
        owner_sub: str,
        idempotency_key: str,
        request: CreateRunRequest,
    ) -> RunResponse:
        if request.scenario_id not in SCENARIO_IDS:
            raise ValueError("unsupported scenario_id")
        if isinstance(request.seed, bool):
            raise ValueError("seed must be an integer")

        now = self._clock().astimezone(UTC).replace(microsecond=0)
        metadata: dict[str, Any] = {
            "scenario_id": request.scenario_id,
            "seed": request.seed if request.seed is not None else self._seed_factory(),
            "base_timestamp": now,
        }
        run = self.storage.create_or_get_run(
            owner_sub,
            idempotency_key,
            _request_hash(request),
            metadata,
        )

        try:
            if run.status == RunStatus.pending_enqueue:
                if run.snapshot_ref is None:
                    preparation = run.to_preparation_record()
                    if preparation is None:
                        raise TemporaryEnqueueError("run preparation metadata is unavailable")
                    snapshot = Snapshot.model_validate(
                        generate_snapshot(
                            preparation.scenario_id,
                            preparation.seed,
                            preparation.base_timestamp,
                            snapshot_id=preparation.snapshot_id,
                            shipment_id=preparation.shipment_id,
                        )
                    )
                    snapshot_ref = self.storage.put_snapshot(snapshot)
                    self.storage.attach_snapshot(run.run_id, snapshot_ref)

                try:
                    self.queue.send_run(run.run_id, run.snapshot_id)
                except QueueError as error:
                    raise TemporaryEnqueueError(
                        f"queue delivery failed; retry the same request for run {run.run_id}"
                    ) from error
                except Exception as error:
                    raise TemporaryEnqueueError(
                        f"queue delivery failed; retry the same request for run {run.run_id}"
                    ) from error
                self.storage.mark_queued(run.run_id)
        except StorageError as error:
            # The response body remains the frozen error contract; this header lets a
            # caller correlate and retry a run that was already durably reserved.
            error.run_id = run.run_id
            raise

        current = self.storage.get_run(run.run_id)
        if current is None:
            raise NotFoundError("run does not exist")
        return RunResponse(
            run_id=current.run_id,
            status=current.status,
            poll_url=f"/v1/runs/{current.run_id}",
        )

    def get_owned_run(self, run_id: str, owner_sub: str) -> Run:
        run = self.storage.get_run(_require_uuid(run_id))
        if run is None or run.owner_sub != owner_sub:
            raise NotFoundError("run does not exist")
        return run

    def get_public_run(self, run_id: str) -> Run:
        run = self.storage.get_public_run(_require_uuid(run_id))
        if run is None or not run.is_public_demo:
            raise NotFoundError("public run does not exist")
        return run

    def get_snapshot(self, run: Run) -> Snapshot:
        if run.snapshot_ref is None:
            raise NotFoundError("snapshot does not exist")
        return self.storage.get_snapshot(run.snapshot_ref)

    def get_report(self, run: Run) -> Report:
        if run.report_ref is None:
            raise ReportNotReadyError("report is not ready")
        return self.storage.get_report(run.report_ref)

    def get_download(self, run: Run) -> dict[str, str]:
        if run.report_ref is None:
            raise ReportNotReadyError("report is not ready")
        return {"url": self.storage.create_report_download_url(run.report_ref, 300)}

    def save_review(self, run: Run, actor_sub: str, request: ReviewRequest) -> Review:
        if run.report_ref is None or run.report_id is None:
            raise ReportNotReadyError("report is not ready")
        return self.storage.save_review(
            run.run_id,
            actor_sub,
            request.report_id,
            request.decision,
            request.note,
        )
