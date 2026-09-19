from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from threading import Barrier

import pytest

from coldchain.contracts.enums import PublicStage, ReviewDecision, RunStatus, StageEventStatus
from coldchain.contracts.schemas import ArtifactRef, Report, Snapshot, StageEvent
from coldchain.simulator import generate_snapshot
from coldchain.storage import (
    ActiveRunLimitExceededError,
    ConditionalCheckFailedError,
    DailyLimitExceededError,
    IdempotencyConflictError,
    NotFoundError,
    StateConflictError,
)
from coldchain.storage.memory import MemoryStorage

from .conftest import BASE_TIME, NOW, run_metadata, stable_uuid


def _create(storage: MemoryStorage, **metadata: object):
    return storage.create_or_get_run(
        "operator-a", "request-key", "body-hash", run_metadata(**metadata)
    )


def _bound_report(
    report: Report,
    *,
    run_id: str,
    snapshot: Snapshot,
    snapshot_ref: ArtifactRef,
    report_id: str | None = None,
) -> Report:
    payload = report.model_dump(mode="python")
    payload.update(
        {
            "report_id": report_id or report.report_id,
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_sha256": snapshot_ref.sha256,
        }
    )
    for evidence in payload["evidence"]:
        evidence["snapshot_id"] = snapshot.snapshot_id
    return Report.model_validate(payload)


def test_run_reservation_persists_retry_identity_atomically(memory: MemoryStorage) -> None:
    first = _create(memory)
    retry = _create(memory)
    assert retry.run_id == first.run_id
    assert retry.preparation == first.preparation
    assert retry.scenario_id == "normal_control"
    assert retry.seed == 17
    assert retry.base_timestamp == BASE_TIME
    regenerated = generate_snapshot(
        retry.scenario_id,
        retry.seed,
        retry.base_timestamp,
        snapshot_id=retry.snapshot_id,
        shipment_id=retry.shipment_id,
    )
    assert regenerated == generate_snapshot(
        first.scenario_id,
        first.seed,
        first.base_timestamp,
        snapshot_id=first.snapshot_id,
        shipment_id=first.shipment_id,
    )


def test_idempotency_conflict_and_active_limit_are_canonical(memory: MemoryStorage) -> None:
    _create(memory)
    with pytest.raises(IdempotencyConflictError):
        memory.create_or_get_run("operator-a", "request-key", "changed", run_metadata())
    with pytest.raises(ActiveRunLimitExceededError):
        memory.create_or_get_run("operator-a", "second-key", "other", run_metadata())


def test_daily_limit_is_atomic() -> None:
    ids = iter(stable_uuid(f"limited-{i}") for i in range(10))
    storage = MemoryStorage(id_factory=lambda: next(ids), clock=lambda: NOW, daily_run_limit=1)
    storage.create_or_get_run("a", "a", "a", run_metadata())
    with pytest.raises(DailyLimitExceededError):
        storage.create_or_get_run("b", "b", "b", run_metadata())


def test_concurrent_requests_cannot_exceed_daily_limit() -> None:
    storage = MemoryStorage(clock=lambda: NOW, daily_run_limit=50)
    for index in range(49):
        storage.create_or_get_run(
            f"operator-{index}", f"key-{index}", f"hash-{index}", run_metadata()
        )
    barrier = Barrier(2)

    def reserve(index: int) -> bool:
        barrier.wait()
        try:
            storage.create_or_get_run(
                f"contender-{index}",
                f"contender-key-{index}",
                f"contender-hash-{index}",
                run_metadata(),
            )
            return True
        except DailyLimitExceededError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, range(2)))

    assert sorted(results) == [False, True]
    with pytest.raises(DailyLimitExceededError):
        storage.create_or_get_run("after-limit", "after", "after", run_metadata())


def test_concurrent_active_run_reservations_are_per_operator() -> None:
    storage = MemoryStorage(clock=lambda: NOW)
    barrier = Barrier(2)

    def reserve_same_operator(index: int) -> bool:
        barrier.wait()
        try:
            storage.create_or_get_run(
                "shared-operator", f"key-{index}", f"hash-{index}", run_metadata()
            )
            return True
        except ActiveRunLimitExceededError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        same_operator = list(executor.map(reserve_same_operator, range(2)))
    assert sorted(same_operator) == [False, True]

    independent = MemoryStorage(clock=lambda: NOW)
    barrier = Barrier(2)

    def reserve_distinct_operator(index: int) -> str:
        barrier.wait()
        return independent.create_or_get_run(
            f"operator-{index}", "shared-key", "shared-hash", run_metadata()
        ).run_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        run_ids = list(executor.map(reserve_distinct_operator, range(2)))
    assert len(set(run_ids)) == 2


def test_snapshot_attach_then_queue_transition_is_safe(memory: MemoryStorage, snapshot) -> None:
    run = _create(
        memory,
        snapshot_id=snapshot.snapshot_id,
        shipment_id=snapshot.shipment_id,
    )
    with pytest.raises(ConditionalCheckFailedError):
        memory.mark_queued(run.run_id)
    reference = memory.put_snapshot(snapshot)
    memory.attach_snapshot(run.run_id, reference)
    memory.mark_queued(run.run_id)
    assert memory.get_run(run.run_id).status == RunStatus.queued
    claim = memory.claim_run(run.run_id, "attempt", NOW, 60)
    assert claim.success
    memory.mark_queued(run.run_id)
    assert memory.get_run(run.run_id).status == RunStatus.running


def test_claim_retry_and_stage_updates_use_canonical_models(memory: MemoryStorage) -> None:
    run = _create(memory)
    assert memory.claim_run(run.run_id, "one", NOW, 30).success
    assert not memory.claim_run(run.run_id, "two", NOW + timedelta(seconds=1), 30).success
    assert memory.claim_run(run.run_id, "two", NOW + timedelta(seconds=31), 30).success
    memory.set_stage(run.run_id, "two", PublicStage.detecting)
    event = StageEvent(
        event_id=stable_uuid("stage-event"),
        stage=PublicStage.detecting,
        started_at=NOW,
        finished_at=NOW,
        status=StageEventStatus.completed,
    )
    memory.append_stage_event(run.run_id, "two", event)
    loaded = memory.get_run(run.run_id)
    assert loaded.stage_events == [event]
    with pytest.raises(ConditionalCheckFailedError, match="stage cannot regress"):
        memory.set_stage(run.run_id, "two", PublicStage.preparing)
    with pytest.raises(ConditionalCheckFailedError):
        memory.set_stage(run.run_id, "one", PublicStage.verifying)


def test_snapshot_and_report_are_immutable_and_digest_checked(
    memory: MemoryStorage, snapshot, report: Report
) -> None:
    snapshot_ref = memory.put_snapshot(snapshot)
    report_ref = memory.put_report(report)
    assert memory.get_snapshot(snapshot_ref) == snapshot
    assert memory.get_report(report_ref) == report
    with pytest.raises(StateConflictError):
        memory.get_report(ArtifactRef(key=report_ref.key, sha256="0" * 64))
    changed = deepcopy(report.model_dump(mode="python"))
    changed["outcome"] = "unresolved"
    with pytest.raises(StateConflictError):
        memory.put_report(Report.model_validate(changed))


@pytest.mark.parametrize("payload", [b"{", b"[]", b"{}"])
def test_schema_corrupt_artifacts_are_explicit_conflicts(
    memory: MemoryStorage, report: Report, payload: bytes
) -> None:
    reference = memory.put_report(report)
    memory._corrupt_artifact_for_test(reference.key, payload)
    matching_reference = ArtifactRef(
        key=reference.key,
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    with pytest.raises(StateConflictError, match="artifact"):
        memory.get_report(matching_reference)


def test_completion_review_public_summary_and_signer(
    memory: MemoryStorage, snapshot: Snapshot, report: Report
) -> None:
    run = _create(
        memory,
        run_id=report.run_id,
        snapshot_id=snapshot.snapshot_id,
        shipment_id=snapshot.shipment_id,
    )
    snapshot_ref = memory.put_snapshot(snapshot)
    memory.attach_snapshot(run.run_id, snapshot_ref)
    memory.claim_run(run.run_id, "attempt", NOW, 60)
    bound_report = _bound_report(
        report,
        run_id=run.run_id,
        snapshot=snapshot,
        snapshot_ref=snapshot_ref,
    )
    report_ref = memory.put_report(bound_report)
    memory.complete_run(run.run_id, "attempt", RunStatus.completed, report_ref, "done")
    completed = memory.get_run(run.run_id)
    assert completed.report_id == bound_report.report_id
    assert completed.report_summary.outcome == bound_report.outcome
    review = memory.save_review(
        run.run_id,
        "actor",
        bound_report.report_id,
        ReviewDecision.acknowledged,
        "checked",
    )
    assert memory.get_run(run.run_id).review == review
    memory.set_public_demo(
        run.run_id,
        {
            "run_id": run.run_id,
            "status": "completed",
            "stage": "ready",
            "created_at": NOW,
            "is_public_demo": True,
            "label": "control",
        },
    )
    assert memory.list_public_runs()[0].run_id == run.run_id
    assert memory.create_report_download_url(report_ref, 60).endswith("expires_in=60")

    next_run = memory.create_or_get_run("operator-a", "next-request", "next-hash", run_metadata())
    assert next_run.run_id != run.run_id


def test_completion_rejects_another_runs_report(
    memory: MemoryStorage, snapshot: Snapshot, report: Report
) -> None:
    run_a = memory.create_or_get_run(
        "operator-a",
        "run-a",
        "hash-a",
        run_metadata(
            run_id=stable_uuid("run-a"),
            snapshot_id=snapshot.snapshot_id,
            shipment_id=snapshot.shipment_id,
        ),
    )
    snapshot_a_ref = memory.put_snapshot(snapshot)
    memory.attach_snapshot(run_a.run_id, snapshot_a_ref)
    memory.claim_run(run_a.run_id, "attempt-a", NOW, 60)

    snapshot_b = Snapshot.model_validate(
        generate_snapshot(
            "normal_control",
            18,
            BASE_TIME,
            snapshot_id=stable_uuid("snapshot-b"),
            shipment_id=stable_uuid("shipment-b"),
        )
    )
    run_b = memory.create_or_get_run(
        "operator-b",
        "run-b",
        "hash-b",
        run_metadata(
            run_id=stable_uuid("run-b"),
            snapshot_id=snapshot_b.snapshot_id,
            shipment_id=snapshot_b.shipment_id,
        ),
    )
    snapshot_b_ref = memory.put_snapshot(snapshot_b)
    memory.attach_snapshot(run_b.run_id, snapshot_b_ref)
    report_b = _bound_report(
        report,
        run_id=run_b.run_id,
        snapshot=snapshot_b,
        snapshot_ref=snapshot_b_ref,
        report_id=stable_uuid("report-b"),
    )
    report_b_ref = memory.put_report(report_b)

    with pytest.raises(
        ConditionalCheckFailedError,
        match="run_id, snapshot_id, snapshot_sha256",
    ):
        memory.complete_run(
            run_a.run_id,
            "attempt-a",
            RunStatus.completed,
            report_b_ref,
            "wrong report",
        )
    assert memory.get_run(run_a.run_id).status == RunStatus.running


def test_stale_attempt_cannot_complete_after_lease_replacement(
    memory: MemoryStorage, snapshot: Snapshot, report: Report
) -> None:
    run = _create(
        memory,
        run_id=report.run_id,
        snapshot_id=snapshot.snapshot_id,
        shipment_id=snapshot.shipment_id,
    )
    snapshot_ref = memory.put_snapshot(snapshot)
    memory.attach_snapshot(run.run_id, snapshot_ref)
    assert memory.claim_run(run.run_id, "attempt-a", NOW, 1).success
    assert memory.claim_run(run.run_id, "attempt-b", NOW + timedelta(seconds=2), 60).success
    bound_report = _bound_report(
        report,
        run_id=run.run_id,
        snapshot=snapshot,
        snapshot_ref=snapshot_ref,
    )
    report_ref = memory.put_report(bound_report)

    with pytest.raises(ConditionalCheckFailedError, match="not current"):
        memory.complete_run(run.run_id, "attempt-a", RunStatus.completed, report_ref, "stale")
    memory.complete_run(run.run_id, "attempt-b", RunStatus.completed, report_ref, "done")
    assert memory.get_run(run.run_id).status == RunStatus.completed


def test_duplicate_completion_cannot_replace_terminal_report(
    memory: MemoryStorage, snapshot: Snapshot, report: Report
) -> None:
    run = _create(
        memory,
        run_id=report.run_id,
        snapshot_id=snapshot.snapshot_id,
        shipment_id=snapshot.shipment_id,
    )
    snapshot_ref = memory.put_snapshot(snapshot)
    memory.attach_snapshot(run.run_id, snapshot_ref)
    memory.claim_run(run.run_id, "attempt", NOW, 60)
    first = _bound_report(
        report,
        run_id=run.run_id,
        snapshot=snapshot,
        snapshot_ref=snapshot_ref,
    )
    second = _bound_report(
        report,
        run_id=run.run_id,
        snapshot=snapshot,
        snapshot_ref=snapshot_ref,
        report_id=stable_uuid("replacement-report"),
    )
    first_ref = memory.put_report(first)
    second_ref = memory.put_report(second)
    memory.complete_run(run.run_id, "attempt", RunStatus.completed, first_ref, "done")

    with pytest.raises(ConditionalCheckFailedError, match="different terminal"):
        memory.complete_run(run.run_id, "attempt", RunStatus.completed, second_ref, "replace")
    memory.mark_queued(run.run_id)
    assert not memory.claim_run(run.run_id, "new-attempt", NOW + timedelta(minutes=5), 60).success
    assert memory.get_run(run.run_id).report_ref == first_ref
    assert memory.get_run(run.run_id).status == RunStatus.completed


def test_failed_completion_does_not_require_report(memory: MemoryStorage) -> None:
    run = _create(memory)
    memory.claim_run(run.run_id, "attempt", NOW, 60)
    memory.complete_run(run.run_id, "attempt", RunStatus.failed, None, "boom")
    failed = memory.get_run(run.run_id)
    assert failed.status == RunStatus.failed
    assert failed.error.message == "boom"


def test_missing_report_artifact_cannot_produce_completed_state(
    memory: MemoryStorage, snapshot: Snapshot, report: Report
) -> None:
    run = _create(
        memory,
        run_id=report.run_id,
        snapshot_id=snapshot.snapshot_id,
        shipment_id=snapshot.shipment_id,
    )
    snapshot_ref = memory.put_snapshot(snapshot)
    memory.attach_snapshot(run.run_id, snapshot_ref)
    memory.claim_run(run.run_id, "attempt", NOW, 60)
    missing_ref = ArtifactRef(
        key=f"reports/{stable_uuid('missing-report')}.json",
        sha256="0" * 64,
    )

    with pytest.raises(NotFoundError, match="artifact"):
        memory.complete_run(
            run.run_id, "attempt", RunStatus.completed, missing_ref, "must not complete"
        )
    assert memory.get_run(run.run_id).status == RunStatus.running
