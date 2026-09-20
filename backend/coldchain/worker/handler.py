"""SQS worker: claim one run, persist verified report, then complete atomically."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from uuid import uuid4

from coldchain.contracts import QueueMessage, StageEvent
from coldchain.contracts.enums import Outcome, PublicStage, RunStatus, StageEventStatus
from coldchain.investigation.agent import Proposer, investigate
from coldchain.investigation.bedrock import BedrockProposer
from coldchain.investigation.rule_proposer import rule_proposal
from coldchain.storage import AwsStorage, StorageProtocol, TemporaryStorageError

logger = logging.getLogger(__name__)
LEASE_SECONDS = 150
_TERMINAL = {RunStatus.completed, RunStatus.needs_review, RunStatus.failed}
_storage: StorageProtocol | None = None


def _event(
    storage: StorageProtocol,
    run_id: str,
    attempt_id: str,
    stage: PublicStage,
    started_at: datetime,
    *,
    tool_name: str | None = None,
    evidence_ids: list[str] | None = None,
) -> None:
    storage.append_stage_event(
        run_id,
        attempt_id,
        StageEvent(
            event_id=str(uuid4()),
            stage=stage,
            tool_name=tool_name,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            status=StageEventStatus.completed,
            evidence_ids=evidence_ids or [],
        ),
    )


def process_message(
    message: QueueMessage,
    storage: StorageProtocol,
    *,
    proposer: Proposer | None = None,
    model_id: str | None = None,
    region_name: str | None = None,
) -> str:
    """Return terminal/active_lease/completed; retry all infrastructure failures."""
    started = time.monotonic()
    # BEDROCK_MODEL_ID is set to "" when Bedrock is disabled, and an empty string
    # would be persisted verbatim as the report's model_id. "No model" is null, not
    # "". Normalising here also keeps the proposer choice below consistent.
    model_id = model_id or None
    run = storage.get_run(message.run_id)
    if run is None:
        raise TemporaryStorageError("queued run is missing")
    if run.status in _TERMINAL:
        return "terminal"
    if run.snapshot_id != message.snapshot_id:
        raise TemporaryStorageError("queue snapshot does not match reserved run")

    attempt_id = str(uuid4())
    claim = storage.claim_run(message.run_id, attempt_id, datetime.now(UTC), LEASE_SECONDS)
    if not claim.success:
        if claim.reason == "terminal":
            return "terminal"
        # Acknowledging an active-lease delivery could orphan a crashed job.
        raise TemporaryStorageError("run is leased by another worker; retry later")

    stage_started = datetime.now(UTC)
    storage.set_stage(run.run_id, attempt_id, PublicStage.detecting)
    current = storage.get_run(run.run_id)
    if current is None or current.snapshot_ref is None:
        raise TemporaryStorageError("claimed run has no durable snapshot")
    snapshot = storage.get_snapshot(current.snapshot_ref)
    if snapshot.snapshot_id != message.snapshot_id:
        raise TemporaryStorageError("stored snapshot does not match queue message")
    _event(storage, run.run_id, attempt_id, PublicStage.detecting, stage_started)

    storage.set_stage(run.run_id, attempt_id, PublicStage.collecting_evidence)

    def on_tool(name: str, began: datetime, ended: datetime, evidence_ids: list[str]) -> None:
        storage.append_stage_event(
            run.run_id,
            attempt_id,
            StageEvent(
                event_id=str(uuid4()),
                stage=PublicStage.collecting_evidence,
                tool_name=name,
                started_at=began,
                finished_at=ended,
                status=StageEventStatus.completed,
                evidence_ids=evidence_ids,
            ),
        )

    if proposer is None:
        if model_id and region_name:
            proposer = BedrockProposer(
                model_id, region_name, deadline=started + 90, on_tool=on_tool
            )
        else:
            # Without Bedrock, fall back to the deterministic rule proposer rather
            # than to no proposer at all. Passing None made investigate() return
            # model_unavailable for every excursion, so the deployed system could
            # detect but never explain. model_id stays None, so the report remains
            # generation_mode=deterministic_only with model_id=null.
            proposer = rule_proposal
    report = investigate(snapshot, run.run_id, proposer, model_id=model_id)
    if report.snapshot_sha256 != current.snapshot_ref.sha256:
        raise TemporaryStorageError("report snapshot digest differs from stored snapshot")
    storage.set_stage(run.run_id, attempt_id, PublicStage.comparing_hypotheses)
    storage.set_stage(run.run_id, attempt_id, PublicStage.verifying)

    # Report persistence precedes the conditional terminal transition. A failed
    # S3 write leaves the run retryable; a stale attempt cannot overwrite another.
    report_ref = storage.put_report(report)
    status = (
        RunStatus.completed
        if report.outcome in {Outcome.no_excursion, Outcome.hypothesis_supported}
        else RunStatus.needs_review
    )
    storage.complete_run(run.run_id, attempt_id, status, report_ref, report.outcome.value)
    logger.info(
        "worker_complete run_id=%s status=%s duration_seconds=%.1f",
        run.run_id,
        status.value,
        time.monotonic() - started,
    )
    return "completed"


def lambda_handler(event: dict, context: object) -> dict[str, str]:
    """Configured for an SQS batch size of one; errors trigger SQS redelivery."""
    del context
    global _storage
    records = event.get("Records", [])
    if len(records) > 1:
        raise ValueError("worker requires SQS batch size one")
    if not records:
        return {"status": "empty"}
    if _storage is None:
        _storage = AwsStorage(
            table_name=os.environ["RUNS_TABLE"],
            bucket_name=os.environ["ARTIFACT_BUCKET"],
        )
    try:
        message = QueueMessage.model_validate(json.loads(records[0]["body"]))
        result = process_message(
            message,
            _storage,
            model_id=os.environ.get("BEDROCK_MODEL_ID"),
            region_name=os.environ.get("AWS_REGION"),
        )
    except Exception as error:
        logger.error("worker_retry category=%s", type(error).__name__)
        raise RuntimeError("worker processing failed; SQS will retry") from None
    return {"status": result}
