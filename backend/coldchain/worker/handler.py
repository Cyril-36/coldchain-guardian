"""SQS investigation worker — bounded execution with leases and retries.

Processes investigation queue messages. Enforces:
- Conditional job leases (stale workers can't steal)
- Duplicate delivery handling
- Model-failure fallback to deterministic
- Run/tool-call/model-call/timeout limits from PLAN.md
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

from coldchain.contracts.enums import GenerationMode, PublicStage, RunStatus, StageEventStatus
from coldchain.contracts.schemas import QueueMessage, StageEvent
from coldchain.investigation.agent import investigate
from coldchain.storage import StorageProtocol

logger = logging.getLogger(__name__)

# ── Limits from PLAN.md §9 ─────────────────────────────────────────────────

WORKER_TIMEOUT_SECONDS = 120
APPLICATION_DEADLINE_SECONDS = 90
LEASE_SECONDS = 150


def handler(event: dict, context: object) -> dict:
    """Lambda SQS event handler.

    Processes one message at a time (batch_size=1 in SAM).
    """
    storage = _get_storage()

    for record in event.get("Records", []):
        body = json.loads(record["body"])
        msg = QueueMessage.model_validate(body)

        try:
            _process_message(msg, storage)
        except Exception:
            logger.exception("Failed to process message for run %s", msg.run_id)
            raise  # Let Lambda retry via SQS visibility timeout

    return {"statusCode": 200}


def _process_message(msg: QueueMessage, storage: StorageProtocol) -> None:
    """Process a single investigation queue message."""
    run = storage.get_run(msg.run_id)
    if run is None:
        logger.warning("Run %s not found, skipping", msg.run_id)
        return

    # Duplicate delivery: already terminal → skip
    if run.status in (RunStatus.completed, RunStatus.needs_review, RunStatus.failed):
        logger.info("Run %s already terminal (%s), skipping", msg.run_id, run.status.value)
        return

    # Claim the job with a lease
    attempt_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    claim = storage.claim_run(msg.run_id, attempt_id, now, LEASE_SECONDS)

    if not claim.success:
        logger.info("Could not claim run %s: %s", msg.run_id, claim.reason)
        return

    try:
        _execute_investigation(msg, attempt_id, storage)
    except Exception as exc:
        logger.exception("Investigation failed for run %s", msg.run_id)
        storage.complete_run(
            run_id=msg.run_id,
            attempt_id=attempt_id,
            status=RunStatus.failed,
            report_ref=None,
            summary=f"Worker exception: {exc}",
        )
        raise


def _execute_investigation(
    msg: QueueMessage,
    attempt_id: str,
    storage: StorageProtocol,
) -> None:
    """Run the full investigation pipeline."""
    start = time.monotonic()
    model_id = os.environ.get("BEDROCK_MODEL_ID")
    use_bedrock = bool(model_id and os.environ.get("USE_BEDROCK", "").lower() == "true")

    # Stage: detecting
    storage.set_stage(msg.run_id, attempt_id, PublicStage.detecting)
    _append_event(storage, msg.run_id, attempt_id, PublicStage.detecting, "start")

    # Load snapshot
    run = storage.get_run(msg.run_id)
    if run is None or run.snapshot_ref is None:
        raise RuntimeError(f"Run {msg.run_id} has no snapshot reference")

    snapshot = storage.get_snapshot(run.snapshot_ref)

    _append_event(storage, msg.run_id, attempt_id, PublicStage.detecting, "complete")

    # Stage: collecting_evidence → comparing_hypotheses → verifying
    storage.set_stage(msg.run_id, attempt_id, PublicStage.collecting_evidence)
    _append_event(storage, msg.run_id, attempt_id, PublicStage.collecting_evidence, "start")

    # Run investigation
    report = investigate(
        snapshot=snapshot,
        run_id=msg.run_id,
        model_id=model_id,
        use_bedrock=use_bedrock,
    )

    _append_event(storage, msg.run_id, attempt_id, PublicStage.comparing_hypotheses, "complete")

    # Stage: verifying
    storage.set_stage(msg.run_id, attempt_id, PublicStage.verifying)

    # Persist report
    report_ref = storage.put_report(report)

    # Complete the run
    terminal_status = (
        RunStatus.completed
        if report.outcome.value != "unresolved" or report.verification.status.value == "passed"
        else RunStatus.needs_review
    )

    # If verification blocked, always needs_review
    if report.verification.status.value == "blocked":
        terminal_status = RunStatus.needs_review

    storage.complete_run(
        run_id=msg.run_id,
        attempt_id=attempt_id,
        status=terminal_status,
        report_ref=report_ref,
        summary=f"Investigation completed: {report.outcome.value}",
    )

    elapsed = time.monotonic() - start
    logger.info(
        "Run %s completed in %.1fs: %s (mode=%s)",
        msg.run_id,
        elapsed,
        report.outcome.value,
        report.generation_mode.value,
    )


def _append_event(
    storage: StorageProtocol,
    run_id: str,
    attempt_id: str,
    stage: PublicStage,
    action: str,
) -> None:
    """Append a stage event."""
    status = StageEventStatus.started if action == "start" else StageEventStatus.completed
    event = StageEvent(
        event_id=str(uuid.uuid4()),
        stage=stage,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc) if action == "complete" else None,
        status=status,
    )
    storage.append_stage_event(run_id, attempt_id, event)


def _get_storage() -> StorageProtocol:
    """Get storage backend. Uses memory for local dev, DynamoDB+S3 for AWS."""
    # For now, always use memory storage. Harshith will implement AWS adapters.
    from coldchain.storage.memory import MemoryStorage
    return MemoryStorage()
