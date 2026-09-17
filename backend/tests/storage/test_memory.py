"""Tests for MemoryStorage implementation."""

import hashlib
from datetime import UTC, datetime

import pytest

from coldchain.contracts.enums import (
    Assessment,
    CoverageStatus,
    EventType,
    GenerationMode,
    HypothesisType,
    NextCheckCode,
    Outcome,
    PublicStage,
    ReviewDecision,
    RunStatus,
    SensorRole,
    StageEventStatus,
    VerificationStatus,
)
from coldchain.contracts.schemas import (
    ArtifactRef,
    Event,
    Hypothesis,
    NextCheck,
    Policy,
    Reading,
    Report,
    Run,
    Sensor,
    SensorMeasurement,
    Snapshot,
    StageEvent,
    Verification,
)
from coldchain.storage import (
    ConditionalCheckFailedError,
    IdempotencyConflictError,
    MemoryStorage,
    NotFoundError,
    StorageProtocol,
)


def _make_snapshot(snapshot_id: str = "snap-1") -> Snapshot:
    cutoff = datetime(2026, 9, 17, 12, 0, 0, tzinfo=UTC)
    return Snapshot(
        snapshot_id=snapshot_id,
        shipment_id="ship-1",
        schema_version="1.0",
        source="simulated",
        cutoff_at=cutoff,
        policy=Policy(
            policy_id="pol-1",
            policy_version="1.0",
            min_c=2.0,
            max_c=8.0,
            expected_interval_seconds=60.0,
            max_gap_seconds=180.0,
        ),
        sensors=[
            Sensor(sensor_id="s1", placement="front", role=SensorRole.reference),
            Sensor(sensor_id="s2", placement="back", role=SensorRole.comparison),
        ],
        readings=[
            Reading(
                event_id="r1",
                sensor_id="s1",
                observed_at=datetime(2026, 9, 17, 11, 30, 0, tzinfo=UTC),
                temperature_c=4.5,
            )
        ],
        events=[
            Event(
                event_id="e1",
                observed_at=datetime(2026, 9, 17, 11, 35, 0, tzinfo=UTC),
                event_type=EventType.door_state,
                value="open",
                source="telemetry",
            )
        ],
    )


def _make_report(report_id: str = "rep-1", run_id: str = "run-1") -> Report:
    now = datetime(2026, 9, 17, 12, 5, 0, tzinfo=UTC)
    return Report(
        report_id=report_id,
        run_id=run_id,
        snapshot_id="snap-1",
        snapshot_sha256="abc123sha",
        schema_version="1.0",
        detector_version="1.0",
        created_at=now,
        cutoff_at=now,
        measurements=[
            SensorMeasurement(
                sensor_id="s1",
                role=SensorRole.reference,
                excursion_detected=False,
                coverage_status=CoverageStatus.full,
            )
        ],
        outcome=Outcome.no_excursion,
        hypotheses=[
            Hypothesis(
                hypothesis=HypothesisType.door_exposure,
                assessment=Assessment.insufficient,
            )
        ],
        next_checks=[
            NextCheck(
                action_code=NextCheckCode.quality_review,
                reason="Routine inspection",
            )
        ],
        verification=Verification(status=VerificationStatus.passed),
        generation_mode=GenerationMode.deterministic_only,
        review_required=False,
    )


def test_implements_protocol():
    storage = MemoryStorage()
    assert isinstance(storage, StorageProtocol)


def test_create_or_get_run_idempotency():
    storage = MemoryStorage()
    owner = "user-123"
    key = "idem-key-1"
    req_hash_1 = "hash-1"
    req_hash_2 = "hash-2"

    run1 = storage.create_or_get_run(owner, key, req_hash_1, {"scenario_id": "door_open"})
    assert isinstance(run1, Run)
    assert run1.owner_sub == owner
    assert run1.scenario_id == "door_open"
    assert run1.status == RunStatus.pending_enqueue
    assert run1.stage == PublicStage.preparing

    # Same key and same hash returns exact same run
    run2 = storage.create_or_get_run(owner, key, req_hash_1, {"scenario_id": "door_open"})
    assert run2.run_id == run1.run_id

    # Same key but different hash raises IdempotencyConflictError (subclass of ValueError)
    with pytest.raises(IdempotencyConflictError):
        storage.create_or_get_run(owner, key, req_hash_2, {"scenario_id": "other"})

    # Different user with same key succeeds
    run_diff_owner = storage.create_or_get_run(
        "user-456", key, req_hash_2, {"scenario_id": "other"}
    )
    assert run_diff_owner.run_id != run1.run_id


def test_put_and_get_snapshot():
    storage = MemoryStorage()
    snap = _make_snapshot("snap-abc")

    ref = storage.put_snapshot(snap)
    assert isinstance(ref, ArtifactRef)
    assert ref.key == "snapshots/snap-abc.json"
    expected_sha = hashlib.sha256(snap.model_dump_json(by_alias=True).encode("utf-8")).hexdigest()
    assert ref.sha256 == expected_sha

    # Retrieve snapshot
    retrieved = storage.get_snapshot(ref)
    assert retrieved.snapshot_id == "snap-abc"

    # Mismatched SHA raises ValueError
    bad_ref = ArtifactRef(key=ref.key, sha256="wrong-sha")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        storage.get_snapshot(bad_ref)

    # Missing snapshot raises NotFoundError (subclass of KeyError)
    with pytest.raises(NotFoundError):
        storage.get_snapshot(ArtifactRef(key="snapshots/missing.json", sha256=""))


def test_put_and_get_report():
    storage = MemoryStorage()
    rep = _make_report("rep-abc", "run-1")

    ref = storage.put_report(rep)
    assert isinstance(ref, ArtifactRef)
    assert ref.key == "reports/rep-abc.json"

    retrieved = storage.get_report(ref)
    assert retrieved.report_id == "rep-abc"

    # Mismatched SHA raises ValueError
    bad_ref = ArtifactRef(key=ref.key, sha256="wrong-sha")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        storage.get_report(bad_ref)

    # Missing report raises NotFoundError
    with pytest.raises(NotFoundError):
        storage.get_report(ArtifactRef(key="reports/missing.json", sha256=""))


def test_claim_run_lifecycle_and_leases():
    storage = MemoryStorage()
    run = storage.create_or_get_run("user-1", "key-1", "hash-1", {})

    t0_str = "2026-09-17T12:00:00Z"
    lease_sec = 60

    # Non-existent run
    res_none = storage.claim_run("nonexistent", "att-1", t0_str, lease_sec)
    assert not res_none.success

    # First claim by att-1
    res1 = storage.claim_run(run.run_id, "att-1", t0_str, lease_sec)
    assert res1.success
    updated_run = storage.get_run(run.run_id)
    assert updated_run.status == RunStatus.running

    # Active lease - another attempt tries to claim at t0 + 30s
    t1_str = "2026-09-17T12:00:30Z"
    res2 = storage.claim_run(run.run_id, "att-2", t1_str, lease_sec)
    assert not res2.success
    assert "Active lease held" in res2.reason

    # Same attempt renewing active lease succeeds
    res_renew = storage.claim_run(run.run_id, "att-1", t1_str, lease_sec)
    assert res_renew.success
    assert "extended" in res_renew.reason

    # After expiry (at 12:01:31Z), att-2 can claim
    t2_str = "2026-09-17T12:01:31Z"
    res3 = storage.claim_run(run.run_id, "att-2", t2_str, lease_sec)
    assert res3.success


def test_stage_and_stage_events():
    storage = MemoryStorage()
    run = storage.create_or_get_run("user-1", "key-1", "hash-1", {})
    storage.claim_run(run.run_id, "att-1", "2026-09-17T12:00:00Z", 120)

    # Set stage
    storage.set_stage(run.run_id, "att-1", PublicStage.detecting)
    assert storage.get_run(run.run_id).stage == PublicStage.detecting

    # Stale attempt fails to set stage
    with pytest.raises(ConditionalCheckFailedError):
        storage.set_stage(run.run_id, "stale-att", PublicStage.comparing_hypotheses)

    # Append stage events - bounded to 40
    for i in range(50):
        evt = StageEvent(
            event_id=f"evt-{i}",
            stage=PublicStage.detecting,
            started_at=datetime(2026, 9, 17, 12, 0, i, tzinfo=UTC),
            status=StageEventStatus.completed,
        )
        storage.append_stage_event(run.run_id, "att-1", evt)

    run_after = storage.get_run(run.run_id)
    assert len(run_after.stage_events) == 40
    assert run_after.stage_events[0].event_id == "evt-0"
    assert run_after.stage_events[-1].event_id == "evt-39"


def test_complete_run_conditional_and_stale_rejection():
    storage = MemoryStorage()
    run = storage.create_or_get_run("user-1", "key-1", "hash-1", {})

    # att-1 claims at 12:00:00 with 10s lease
    storage.claim_run(run.run_id, "att-1", "2026-09-17T12:00:00Z", 10)

    # Lease expires, att-2 claims at 12:00:20
    storage.claim_run(run.run_id, "att-2", "2026-09-17T12:00:20Z", 60)

    # Stale worker att-1 tries to complete run
    with pytest.raises(ConditionalCheckFailedError, match="Stale attempt"):
        storage.complete_run(
            run.run_id,
            "att-1",
            RunStatus.completed,
            ArtifactRef(key="reports/rep-1.json", sha256="sha"),
            "Completed summary",
        )

    # Valid worker att-2 completes run
    storage.complete_run(
        run.run_id,
        "att-2",
        RunStatus.completed,
        ArtifactRef(key="reports/rep-1.json", sha256="sha"),
        "Success",
    )
    finished_run = storage.get_run(run.run_id)
    assert finished_run.status == RunStatus.completed
    assert finished_run.stage == PublicStage.ready

    # Cannot claim or complete already terminal run
    claim_again = storage.claim_run(run.run_id, "att-3", "2026-09-17T12:00:30Z", 60)
    assert not claim_again.success
    assert "terminal status" in claim_again.reason

    with pytest.raises(ConditionalCheckFailedError, match="terminal"):
        storage.complete_run(
            run.run_id,
            "att-2",
            RunStatus.completed,
            None,
            "",
        )


def test_save_review():
    storage = MemoryStorage()
    run = storage.create_or_get_run("user-1", "key-1", "hash-1", {})
    storage.claim_run(run.run_id, "att-1", "2026-09-17T12:00:00Z", 60)

    # Review before report exists raises ValueError
    with pytest.raises(ValueError, match="no report"):
        storage.save_review(
            run.run_id, "reviewer-1", "rep-1", ReviewDecision.acknowledged, "looks good"
        )

    # Put report and complete run
    rep = _make_report("rep-1", run.run_id)
    rep_ref = storage.put_report(rep)
    storage.complete_run(run.run_id, "att-1", RunStatus.completed, rep_ref, "Done")

    # Mismatched report_id raises ValueError
    with pytest.raises(ValueError, match="Report ID mismatch"):
        storage.save_review(
            run.run_id, "reviewer-1", "rep-different", ReviewDecision.acknowledged, "note"
        )

    # Valid review
    rev = storage.save_review(
        run.run_id, "reviewer-1", "rep-1", ReviewDecision.acknowledged, "Approved"
    )
    assert rev.run_id == run.run_id
    assert rev.actor_sub == "reviewer-1"
    assert rev.decision == ReviewDecision.acknowledged
    assert rev.note == "Approved"

    updated_run = storage.get_run(run.run_id)
    assert updated_run.review is not None
    assert updated_run.review.note == "Approved"


def test_enqueue_run():
    storage = MemoryStorage()
    run = storage.create_or_get_run("user-1", "key-1", "hash-1", {})
    assert run.status == RunStatus.pending_enqueue

    storage.enqueue_run(run.run_id, "snap-123", "1.0")
    assert len(storage.queue) == 1
    assert storage.queue[0].run_id == run.run_id
    assert storage.queue[0].snapshot_id == "snap-123"

    # Status updated to queued
    assert storage.get_run(run.run_id).status == RunStatus.queued


def test_list_public_runs():
    storage = MemoryStorage()

    # Private run
    storage.create_or_get_run("u1", "k1", "h1", {"is_public_demo": False})

    # Public run 1
    t1 = datetime(2026, 9, 17, 10, 0, 0, tzinfo=UTC)
    run_pub1 = storage.create_or_get_run(
        "u1", "k2", "h2", {"is_public_demo": True, "created_at": t1}
    )

    # Public run 2 (newer) with report
    t2 = datetime(2026, 9, 17, 11, 0, 0, tzinfo=UTC)
    run_pub2 = storage.create_or_get_run(
        "u1", "k3", "h3", {"is_public_demo": True, "created_at": t2}
    )
    rep = _make_report("rep-pub", run_pub2.run_id)
    rep_ref = storage.put_report(rep)
    storage.claim_run(run_pub2.run_id, "att-1", "2026-09-17T11:00:00Z", 60)
    storage.complete_run(run_pub2.run_id, "att-1", RunStatus.completed, rep_ref, "Done")

    public_runs = storage.list_public_runs()
    assert len(public_runs) == 2
    # Ordered descending by created_at
    assert public_runs[0].run_id == run_pub2.run_id
    assert public_runs[0].outcome == Outcome.no_excursion
    assert public_runs[0].is_public_demo is True

    assert public_runs[1].run_id == run_pub1.run_id
    assert public_runs[1].outcome is None
