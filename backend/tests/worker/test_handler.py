"""Worker state transitions under duplicate delivery and storage failure."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from coldchain.contracts import QueueMessage, Snapshot
from coldchain.contracts.enums import RunStatus
from coldchain.simulator import generate_snapshot
from coldchain.storage import MemoryStorage, TemporaryStorageError
from coldchain.worker.handler import process_message

NOW = datetime(2026, 9, 18, tzinfo=UTC)


def _queued(scenario: str, storage: MemoryStorage) -> QueueMessage:
    run = storage.create_or_get_run(
        "operator-a",
        str(uuid4()),
        str(uuid4()),
        {"scenario_id": scenario, "seed": 17, "base_timestamp": NOW},
    )
    snapshot = Snapshot.model_validate(
        generate_snapshot(
            scenario,
            17,
            NOW,
            snapshot_id=run.snapshot_id,
            shipment_id=run.shipment_id,
        )
    )
    storage.attach_snapshot(run.run_id, storage.put_snapshot(snapshot))
    storage.mark_queued(run.run_id)
    return QueueMessage(run_id=run.run_id, snapshot_id=run.snapshot_id)


def test_normal_run_completes_once_without_model_work() -> None:
    storage = MemoryStorage()
    message = _queued("normal_control", storage)
    calls = 0

    def model(_ctx):
        nonlocal calls
        calls += 1
        raise AssertionError("normal run must skip model")

    assert process_message(message, storage, proposer=model) == "completed"
    assert process_message(message, storage, proposer=model) == "terminal"
    run = storage.get_run(message.run_id)
    assert run is not None and run.status == RunStatus.completed
    assert run.report_ref is not None
    assert storage.get_report(run.report_ref).outcome == "no_excursion"
    assert calls == 0


def test_model_unavailable_persists_reviewable_deterministic_report() -> None:
    storage = MemoryStorage()
    message = _queued("door_exposure", storage)

    assert process_message(message, storage) == "completed"
    run = storage.get_run(message.run_id)
    assert run is not None and run.status == RunStatus.needs_review
    assert run.report_ref is not None
    report = storage.get_report(run.report_ref)
    assert report.generation_mode == "deterministic_only"
    assert report.verification.status == "blocked"
    assert any(m.estimated_out_of_range_seconds > 0 for m in report.measurements)


def test_mismatched_queue_snapshot_is_not_claimed() -> None:
    storage = MemoryStorage()
    message = _queued("normal_control", storage)
    mismatched = QueueMessage(run_id=message.run_id, snapshot_id=str(uuid4()))

    with pytest.raises(TemporaryStorageError, match="queue snapshot"):
        process_message(mismatched, storage)
    assert storage.get_run(message.run_id).status == RunStatus.queued


def test_active_lease_is_retried_instead_of_acked() -> None:
    storage = MemoryStorage()
    message = _queued("normal_control", storage)
    storage.claim_run(message.run_id, str(uuid4()), datetime.now(UTC), 150)

    with pytest.raises(TemporaryStorageError, match="leased"):
        process_message(message, storage)
    assert storage.get_run(message.run_id).status == RunStatus.running


class ReportWriteFailure(MemoryStorage):
    def put_report(self, report):
        raise TemporaryStorageError("S3 unavailable")


def test_report_write_failure_never_marks_run_complete() -> None:
    storage = ReportWriteFailure()
    message = _queued("normal_control", storage)

    with pytest.raises(TemporaryStorageError, match="S3 unavailable"):
        process_message(message, storage)
    run = storage.get_run(message.run_id)
    assert run is not None and run.status == RunStatus.running
    assert run.report_ref is None
