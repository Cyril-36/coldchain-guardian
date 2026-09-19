"""Reconciling runs abandoned after SQS exhausted its retries."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from coldchain.contracts import QueueMessage, Snapshot
from coldchain.contracts.enums import RunStatus
from coldchain.simulator import generate_snapshot
from coldchain.storage import MemoryStorage
from coldchain.worker.handler import process_message
from coldchain.worker.reconcile import FAILURE_SUMMARY, reconcile_runs

NOW = datetime(2026, 9, 19, tzinfo=UTC)
LATER = NOW + timedelta(hours=1)


def _queued(storage: MemoryStorage, scenario: str = "door_exposure") -> str:
    run = storage.create_or_get_run(
        "operator-a",
        str(uuid4()),
        str(uuid4()),
        {"scenario_id": scenario, "seed": 17, "base_timestamp": NOW},
    )
    snapshot = Snapshot.model_validate(
        generate_snapshot(
            scenario, 17, NOW, snapshot_id=run.snapshot_id, shipment_id=run.shipment_id
        )
    )
    storage.attach_snapshot(run.run_id, storage.put_snapshot(snapshot))
    storage.mark_queued(run.run_id)
    return run.run_id


def test_abandoned_run_is_failed_and_its_lease_released() -> None:
    """The case SQS cannot resolve: retries exhausted, run left running forever."""
    storage = MemoryStorage()
    run_id = _queued(storage)
    storage.claim_run(run_id, str(uuid4()), NOW, 150)

    stuck = storage.get_run(run_id)
    assert stuck.status == RunStatus.running
    assert stuck.report_ref is None

    result = reconcile_runs(storage, [run_id], now=LATER)
    assert result.failed == [run_id]

    settled = storage.get_run(run_id)
    assert settled.status == RunStatus.failed
    assert settled.report_ref is None
    assert settled.completed_at is not None


def test_run_with_an_unexpired_lease_is_left_alone() -> None:
    """A live worker may still be holding it; failing it would race its completion."""
    storage = MemoryStorage()
    run_id = _queued(storage)
    storage.claim_run(run_id, str(uuid4()), NOW, 150)

    result = reconcile_runs(storage, [run_id], now=NOW + timedelta(seconds=30))
    assert result.failed == []
    assert result.lease_still_active == [run_id]
    assert storage.get_run(run_id).status == RunStatus.running


def test_completed_run_is_never_overwritten() -> None:
    storage = MemoryStorage()
    run_id = _queued(storage, "normal_control")
    process_message(
        QueueMessage(run_id=run_id, snapshot_id=storage.get_run(run_id).snapshot_id), storage
    )
    before = storage.get_run(run_id)

    result = reconcile_runs(storage, [run_id], now=LATER)
    assert result.failed == []
    assert result.already_terminal == [run_id]
    assert storage.get_run(run_id).report_ref == before.report_ref
    assert storage.get_run(run_id).status == before.status


def test_dry_run_reports_without_changing_anything() -> None:
    storage = MemoryStorage()
    run_id = _queued(storage)
    storage.claim_run(run_id, str(uuid4()), NOW, 150)

    result = reconcile_runs(storage, [run_id], now=LATER, dry_run=True)
    assert result.failed == [run_id]
    assert storage.get_run(run_id).status == RunStatus.running


def test_unknown_and_duplicate_run_ids_are_handled() -> None:
    storage = MemoryStorage()
    run_id = _queued(storage)
    storage.claim_run(run_id, str(uuid4()), NOW, 150)
    missing = str(uuid4())

    result = reconcile_runs(storage, [run_id, run_id, missing], now=LATER)
    assert result.failed == [run_id]
    assert result.missing == [missing]


def test_reconciled_run_carries_a_visible_reason() -> None:
    storage = MemoryStorage()
    run_id = _queued(storage)
    storage.claim_run(run_id, str(uuid4()), NOW, 150)
    reconcile_runs(storage, [run_id], now=LATER)

    run = storage.get_run(run_id)
    assert run.status == RunStatus.failed
    # The operator must be able to see why, not just that it stopped.
    assert run.error is not None
    assert run.error.message == FAILURE_SUMMARY
    assert run.error.retryable is False

    # And the operator's active-run slot is released, so they are not locked out of
    # starting another investigation by a run that can never finish.
    second = _queued(storage)
    assert storage.get_run(second) is not None
