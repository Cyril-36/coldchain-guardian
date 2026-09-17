"""Comprehensive tests for canonical Pydantic v2 schemas and validation rules."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from coldchain.contracts.enums import (
    CoverageStatus,
    EvidenceKind,
    GenerationMode,
    HypothesisType,
    NextCheckCode,
    Outcome,
    PublicStage,
    ReviewDecision,
    RunStatus,
    StageEventStatus,
    VerificationStatus,
)
from coldchain.contracts.schemas import (
    MAX_SNAPSHOT_EVENTS,
    MAX_SNAPSHOT_READINGS,
    ArtifactRef,
    ClaimResult,
    EvidenceInterval,
    EvidenceRef,
    Hypothesis,
    NextCheck,
    Policy,
    PreparationRecord,
    QueueMessage,
    Reading,
    Report,
    ReportSummary,
    Review,
    Run,
    RunSummary,
    SensorMeasurement,
    Snapshot,
    StageEvent,
    Verification,
    snapshot_sha256,
)


def _make_valid_snapshot(**overrides: Any) -> dict[str, Any]:
    """Helper to produce a minimal valid snapshot dictionary."""
    ref_sensor_id = "00000000-0000-4000-8000-000000000001"
    cmp_sensor_id = "00000000-0000-4000-8000-000000000002"

    payload: dict[str, Any] = {
        "snapshot_id": "11111111-1111-4111-8111-111111111111",
        "shipment_id": "22222222-2222-4222-8222-222222222222",
        "schema_version": "1.0",
        "source": "simulated",
        "cutoff_at": "2026-09-17T10:45:00Z",
        "policy": {
            "policy_id": "policy-001",
            "policy_version": "1.0",
            "min_c": 2.0,
            "max_c": 8.0,
            "expected_interval_seconds": 60.0,
            "max_gap_seconds": 120.0,
        },
        "sensors": [
            {"sensor_id": ref_sensor_id, "placement": "front_air", "role": "reference"},
            {"sensor_id": cmp_sensor_id, "placement": "rear_air", "role": "comparison"},
        ],
        "readings": [
            {
                "event_id": "33333333-3333-4333-8333-333333333331",
                "sensor_id": ref_sensor_id,
                "observed_at": "2026-09-17T10:00:00Z",
                "temperature_c": 5.0,
            },
            {
                "event_id": "33333333-3333-4333-8333-333333333332",
                "sensor_id": cmp_sensor_id,
                "observed_at": "2026-09-17T10:00:00Z",
                "temperature_c": 5.2,
            },
        ],
        "events": [
            {
                "event_id": "44444444-4444-4444-8444-444444444441",
                "observed_at": "2026-09-17T10:00:00Z",
                "event_type": "door_state",
                "value": "closed",
                "source": "simulated_controller",
            }
        ],
    }
    payload.update(overrides)
    return payload


# ── UUID Validation Tests ───────────────────────────────────────────────────


def test_uuid_validation_accepts_valid_lowercase_uuid() -> None:
    data = _make_valid_snapshot()
    snapshot = Snapshot.model_validate(data)
    assert snapshot.snapshot_id == "11111111-1111-4111-8111-111111111111"


def test_uuid_validation_rejects_non_uuid_string() -> None:
    data = _make_valid_snapshot(snapshot_id="not-a-valid-uuid")
    with pytest.raises(ValidationError, match="Invalid UUID"):
        Snapshot.model_validate(data)


def test_uuid_validation_rejects_uppercase_uuid() -> None:
    data = _make_valid_snapshot(snapshot_id="abcdef01-2345-4678-89ab-cdef01234567".upper())
    with pytest.raises(ValidationError, match="normalized lowercase"):
        Snapshot.model_validate(data)


def test_uuid_validation_rejects_non_string_type() -> None:
    data = _make_valid_snapshot(snapshot_id=12345678)
    with pytest.raises(ValidationError, match="string UUID"):
        Snapshot.model_validate(data)


# ── UTC Z Timestamp Tests ───────────────────────────────────────────────────


def test_timestamp_requires_z_suffix() -> None:
    data = _make_valid_snapshot(cutoff_at="2026-09-17T10:45:00+00:00")
    with pytest.raises(ValidationError, match="ending in Z"):
        Snapshot.model_validate(data)


def test_timestamp_rejects_naive_iso() -> None:
    data = _make_valid_snapshot(cutoff_at="2026-09-17T10:45:00")
    with pytest.raises(ValidationError, match="ending in Z"):
        Snapshot.model_validate(data)


def test_timestamp_serializes_with_z() -> None:
    data = _make_valid_snapshot()
    snapshot = Snapshot.model_validate(data)
    dumped = snapshot.model_dump(mode="json")
    assert dumped["cutoff_at"] == "2026-09-17T10:45:00Z"


def test_timestamp_accepts_datetime_object() -> None:
    reading = Reading(
        event_id="33333333-3333-4333-8333-333333333331",
        sensor_id="00000000-0000-4000-8000-000000000001",
        observed_at=datetime(2026, 9, 17, 10, 0, 0, tzinfo=UTC),
        temperature_c=5.0,
    )
    assert reading.observed_at == datetime(2026, 9, 17, 10, 0, 0, tzinfo=UTC)
    assert reading.model_dump(mode="json")["observed_at"] == "2026-09-17T10:00:00Z"


# ── Finite Number Tests ─────────────────────────────────────────────────────


@pytest.mark.parametrize("bad_val", [float("nan"), float("inf"), float("-inf")])
def test_temperature_rejects_non_finite_values(bad_val: float) -> None:
    with pytest.raises(ValidationError, match="finite"):
        Reading(
            event_id="33333333-3333-4333-8333-333333333331",
            sensor_id="00000000-0000-4000-8000-000000000001",
            observed_at="2026-09-17T10:00:00Z",
            temperature_c=bad_val,
        )


def test_policy_rejects_bool_for_numeric() -> None:
    with pytest.raises(ValidationError, match="numeric"):
        Policy(
            policy_id="policy-001",
            policy_version="1.0",
            min_c=True,  # type: ignore[arg-type]
            max_c=8.0,
            expected_interval_seconds=60.0,
            max_gap_seconds=120.0,
        )


def test_policy_requires_min_below_max() -> None:
    with pytest.raises(ValidationError, match="must be strictly below max_c"):
        Policy(
            policy_id="policy-001",
            policy_version="1.0",
            min_c=8.0,
            max_c=2.0,
            expected_interval_seconds=60.0,
            max_gap_seconds=120.0,
        )


def test_policy_requires_positive_intervals() -> None:
    with pytest.raises(ValidationError, match="must be positive"):
        Policy(
            policy_id="policy-001",
            policy_version="1.0",
            min_c=2.0,
            max_c=8.0,
            expected_interval_seconds=-60.0,
            max_gap_seconds=120.0,
        )


# ── Strict Base (extra="forbid") ───────────────────────────────────────────


def test_strict_base_rejects_extra_fields() -> None:
    data = _make_valid_snapshot(unexpected_extra_field="should_fail")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Snapshot.model_validate(data)


# ── Snapshot Constraints: Limits, Cutoff, Duplicates, Sensors ───────────────


def test_snapshot_requires_exactly_one_reference_sensor() -> None:
    # Zero reference sensors
    data = _make_valid_snapshot(
        sensors=[
            {
                "sensor_id": "00000000-0000-4000-8000-000000000001",
                "placement": "front",
                "role": "comparison",
            },
            {
                "sensor_id": "00000000-0000-4000-8000-000000000002",
                "placement": "rear",
                "role": "comparison",
            },
        ]
    )
    with pytest.raises(ValidationError, match="exactly one reference sensor"):
        Snapshot.model_validate(data)

    # Two reference sensors
    data2 = _make_valid_snapshot(
        sensors=[
            {
                "sensor_id": "00000000-0000-4000-8000-000000000001",
                "placement": "front",
                "role": "reference",
            },
            {
                "sensor_id": "00000000-0000-4000-8000-000000000002",
                "placement": "rear",
                "role": "reference",
            },
        ]
    )
    with pytest.raises(ValidationError, match="exactly one reference sensor"):
        Snapshot.model_validate(data2)


def test_snapshot_rejects_duplicate_sensor_ids() -> None:
    same_id = "00000000-0000-4000-8000-000000000001"
    data = _make_valid_snapshot(
        sensors=[
            {"sensor_id": same_id, "placement": "front", "role": "reference"},
            {"sensor_id": same_id, "placement": "rear", "role": "comparison"},
        ]
    )
    with pytest.raises(ValidationError, match="Duplicate sensor_id"):
        Snapshot.model_validate(data)


def test_snapshot_rejects_reading_with_unknown_sensor_id() -> None:
    data = _make_valid_snapshot()
    data["readings"][0]["sensor_id"] = "99999999-9999-4999-8999-999999999999"
    with pytest.raises(ValidationError, match="references unknown sensor_id"):
        Snapshot.model_validate(data)


def test_snapshot_rejects_reading_after_cutoff() -> None:
    data = _make_valid_snapshot()
    data["readings"][0]["observed_at"] = "2026-09-17T10:45:01Z"
    with pytest.raises(ValidationError, match="occurs after cutoff"):
        Snapshot.model_validate(data)


def test_snapshot_rejects_event_after_cutoff() -> None:
    data = _make_valid_snapshot()
    data["events"][0]["observed_at"] = "2026-09-17T10:45:01Z"
    with pytest.raises(ValidationError, match="occurs after cutoff"):
        Snapshot.model_validate(data)


def test_snapshot_allows_identical_duplicate_event_id() -> None:
    data = _make_valid_snapshot()
    # Add identical copy of first reading
    data["readings"].append(dict(data["readings"][0]))
    snapshot = Snapshot.model_validate(data)
    assert len(snapshot.readings) == 3


def test_snapshot_rejects_conflicting_duplicate_event_id() -> None:
    data = _make_valid_snapshot()
    conflicting = dict(data["readings"][0])
    conflicting["temperature_c"] = 9.99  # different temperature
    data["readings"].append(conflicting)
    with pytest.raises(ValidationError, match="Conflicting duplicate event_id"):
        Snapshot.model_validate(data)


def test_snapshot_rejects_exceeding_max_readings() -> None:
    ref_sensor_id = "00000000-0000-4000-8000-000000000001"
    readings = [
        {
            "event_id": f"{i:08x}-0000-4000-8000-000000000000",
            "sensor_id": ref_sensor_id,
            "observed_at": "2026-09-17T10:00:00Z",
            "temperature_c": 5.0,
        }
        for i in range(MAX_SNAPSHOT_READINGS + 1)
    ]
    data = _make_valid_snapshot(readings=readings)
    with pytest.raises(ValidationError, match="Readings count"):
        Snapshot.model_validate(data)


def test_snapshot_rejects_exceeding_max_events() -> None:
    events = [
        {
            "event_id": f"{i:08x}-0000-4000-8000-000000000000",
            "observed_at": "2026-09-17T10:00:00Z",
            "event_type": "door_state",
            "value": "closed",
            "source": "simulated",
        }
        for i in range(MAX_SNAPSHOT_EVENTS + 1)
    ]
    data = _make_valid_snapshot(events=events)
    with pytest.raises(ValidationError, match="Events count"):
        Snapshot.model_validate(data)


def test_snapshot_rejects_exceeding_1mib_serialized() -> None:
    data = _make_valid_snapshot()
    # Bloat the placement string to push past 1 MiB
    data["sensors"][0]["placement"] = "x" * 1_050_000
    with pytest.raises(ValidationError, match="exceeds 1 MiB"):
        Snapshot.model_validate(data)


def test_snapshot_sha256_computes_consistent_digest() -> None:
    data = _make_valid_snapshot()
    snap1 = Snapshot.model_validate(data)
    snap2 = Snapshot.model_validate(data)
    digest1 = snapshot_sha256(snap1)
    digest2 = snapshot_sha256(snap2)
    assert len(digest1) == 64
    assert digest1 == digest2


# ── NextCheck Field Aliases & Coverage Status Tests ─────────────────────────


def test_next_check_accepts_action_code_and_evidence_ids() -> None:
    nc = NextCheck.model_validate({
        "action_code": "inspect_door",
        "reason": "Check door latch",
        "evidence_ids": ["00000000-0000-4000-8000-000000000001"],
    })
    assert nc.action_code == NextCheckCode.inspect_door
    assert nc.evidence_ids == ["00000000-0000-4000-8000-000000000001"]


def test_next_check_accepts_code_and_related_evidence_ids_aliases() -> None:
    nc = NextCheck.model_validate({
        "code": "quality_review",
        "reason": "QA review required",
        "related_evidence_ids": ["00000000-0000-4000-8000-000000000002"],
    })
    assert nc.action_code == NextCheckCode.quality_review
    assert nc.evidence_ids == ["00000000-0000-4000-8000-000000000002"]


def test_coverage_status_enum_values() -> None:
    assert CoverageStatus.complete.value == "complete"
    assert CoverageStatus.partial.value == "partial"
    assert CoverageStatus.insufficient.value == "insufficient"


def test_next_check_wire_serialization() -> None:
    nc = NextCheck(
        code=NextCheckCode.inspect_door,
        reason="Check door latch",
        related_evidence_ids=["00000000-0000-4000-8000-000000000001"],
    )
    dumped = nc.model_dump(mode="json")
    assert dumped == {
        "code": "inspect_door",
        "reason": "Check door latch",
        "related_evidence_ids": ["00000000-0000-4000-8000-000000000001"],
    }


def test_sensor_measurement_no_role_attribute() -> None:
    # SensorMeasurement must NOT accept role (role belongs to Sensor in Snapshot)
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SensorMeasurement.model_validate({
            "sensor_id": "55555555-5555-4555-8555-555555555555",
            "role": "reference",
            "first_observed_out_at": None,
            "last_observed_out_at": None,
            "estimated_out_of_range_seconds": 0.0,
            "unknown_duration_seconds": 0.0,
            "sample_count": 10,
            "observed_min_c": 4.0,
            "observed_max_c": 5.0,
            "censored_start": False,
            "censored_end": False,
            "coverage_status": "complete",
            "evidence_ids": [],
        })


# ── Review & Run Model Tests ────────────────────────────────────────────────


def test_review_note_length_limit() -> None:
    review = Review(
        run_id="11111111-1111-4111-8111-111111111111",
        actor_sub="operator-001",
        report_id="22222222-2222-4222-8222-222222222222",
        decision=ReviewDecision.acknowledged,
        note="a" * 1000,
        reviewed_at="2026-09-17T12:00:00Z",
    )
    assert len(review.note) == 1000

    with pytest.raises(ValidationError, match="at most 1000 characters"):
        Review(
            run_id="11111111-1111-4111-8111-111111111111",
            actor_sub="operator-001",
            report_id="22222222-2222-4222-8222-222222222222",
            decision=ReviewDecision.acknowledged,
            note="a" * 1001,
            reviewed_at="2026-09-17T12:00:00Z",
        )


def test_review_reviewed_at_wire_format_and_created_at_alias() -> None:
    r1 = Review.model_validate({
        "run_id": "11111111-1111-4111-8111-111111111111",
        "report_id": "22222222-2222-4222-8222-222222222222",
        "decision": "acknowledged",
        "note": "All good",
        "actor_sub": "operator-001",
        "created_at": "2026-09-17T12:00:00Z",
    })
    assert r1.created_at.isoformat().endswith("+00:00")
    assert r1.reviewed_at == r1.created_at
    dumped1 = r1.model_dump(mode="json")
    assert "reviewed_at" in dumped1
    assert dumped1["reviewed_at"] == "2026-09-17T12:00:00Z"

    r2 = Review.model_validate({
        "run_id": "11111111-1111-4111-8111-111111111111",
        "report_id": "22222222-2222-4222-8222-222222222222",
        "decision": "acknowledged",
        "note": "All good",
        "actor_sub": "operator-001",
        "reviewed_at": "2026-09-17T12:00:00Z",
    })
    assert r2.reviewed_at == r2.created_at
    dumped2 = r2.model_dump(mode="json")
    assert dumped2["reviewed_at"] == "2026-09-17T12:00:00Z"


def test_run_stage_events_bounded_at_40() -> None:
    events_40 = [
        StageEvent(
            event_id=f"{i:08x}-0000-4000-8000-000000000000",
            stage=PublicStage.detecting,
            started_at="2026-09-17T10:00:00Z",
            status=StageEventStatus.completed,
        )
        for i in range(40)
    ]
    run = Run(
        run_id="11111111-1111-4111-8111-111111111111",
        shipment_id="22222222-2222-4222-8222-222222222222",
        snapshot_id="33333333-3333-4333-8333-333333333333",
        status=RunStatus.running,
        stage=PublicStage.detecting,
        stage_events=events_40,
        created_at="2026-09-17T10:00:00Z",
    )
    assert len(run.stage_events) == 40

    events_41 = events_40 + [
        StageEvent(
            event_id="00000041-0000-4000-8000-000000000000",
            stage=PublicStage.detecting,
            started_at="2026-09-17T10:00:00Z",
            status=StageEventStatus.completed,
        )
    ]
    with pytest.raises(ValidationError, match="cannot exceed 40 items"):
        Run(
            run_id="11111111-1111-4111-8111-111111111111",
            shipment_id="22222222-2222-4222-8222-222222222222",
            snapshot_id="33333333-3333-4333-8333-333333333333",
            status=RunStatus.running,
            stage=PublicStage.detecting,
            stage_events=events_41,
            created_at="2026-09-17T10:00:00Z",
        )


def test_run_serializes_wire_fields_and_excludes_storage_internals() -> None:
    run = Run(
        run_id="11111111-1111-4111-8111-111111111111",
        shipment_id="22222222-2222-4222-8222-222222222222",
        snapshot_id="33333333-3333-4333-8333-333333333333",
        status=RunStatus.queued,
        stage=PublicStage.preparing,
        created_at="2026-09-17T10:00:00Z",
        owner_sub="operator-sub-123",
        scenario_id="scenario-001",
        attempt_id="attempt-001",
        snapshot_ref=ArtifactRef(
            key="snapshots/snap.json",
            sha256="1dbe7dc1667e3e5eccb954402eeee1a6b06237001bc82ff6953459d8b3b6c8ea",
        ),
    )
    dumped = run.model_dump(mode="json")
    assert dumped["run_id"] == "11111111-1111-4111-8111-111111111111"
    assert dumped["shipment_id"] == "22222222-2222-4222-8222-222222222222"
    assert dumped["snapshot_id"] == "33333333-3333-4333-8333-333333333333"
    assert "owner_sub" not in dumped
    assert "scenario_id" not in dumped
    assert "attempt_id" not in dumped
    assert "snapshot_ref" not in dumped
    assert "report_ref" not in dumped
    assert "lease_expires_at" not in dumped


def test_artifact_ref_validates_sha256_length_and_hex() -> None:
    ref = ArtifactRef(
        key="snapshots/1.json",
        sha256="1dbe7dc1667e3e5eccb954402eeee1a6b06237001bc82ff6953459d8b3b6c8ea",
    )
    assert len(ref.sha256) == 64

    with pytest.raises(ValidationError, match="64-character lowercase hex"):
        ArtifactRef(key="snapshots/1.json", sha256="invalid-sha")


def test_claim_result_model() -> None:
    cr = ClaimResult(success=True, reason="claimed lease")
    assert cr.success is True
    assert cr.reason == "claimed lease"


def test_queue_message_model() -> None:
    qm = QueueMessage(
        run_id="11111111-1111-4111-8111-111111111111",
        snapshot_id="22222222-2222-4222-8222-222222222222",
    )
    assert qm.schema_version == "1.0"


# ── Evidence & Report Tests ─────────────────────────────────────────────────


def test_evidence_interval_validation() -> None:
    interval = EvidenceInterval(
        start_at="2026-09-17T10:00:00Z",
        end_at="2026-09-17T10:15:00Z",
    )
    assert interval.start_at <= interval.end_at

    with pytest.raises(ValidationError, match="start_at cannot be after end_at"):
        EvidenceInterval(
            start_at="2026-09-17T10:15:00Z",
            end_at="2026-09-17T10:00:00Z",
        )


def _make_valid_report(**overrides: Any) -> dict[str, Any]:
    snapshot_id = "11111111-1111-4111-8111-111111111111"
    ev_id = "00000000-0000-4000-8000-000000000001"
    ev_ref = {
        "evidence_id": ev_id,
        "snapshot_id": snapshot_id,
        "kind": "event",
        "record_ids": ["22222222-2222-4222-8222-222222222222"],
        "summary": "Door open event at 10:10:00Z",
        "observed_at": "2026-09-17T10:10:00Z",
    }
    payload: dict[str, Any] = {
        "report_id": "33333333-3333-4333-8333-333333333333",
        "run_id": "44444444-4444-4444-8444-444444444444",
        "snapshot_id": snapshot_id,
        "snapshot_sha256": "5c957e84ca3b58be421ec56db1e26aa54f7627a8e2cb962c040d12e841fa5a72",
        "schema_version": "1.0",
        "detector_version": "1.0.0",
        "prompt_version": "1.0.0",
        "model_id": "amazon.nova-lite-v1:0",
        "created_at": "2026-09-17T10:50:00Z",
        "cutoff_at": "2026-09-17T10:45:00Z",
        "measurements": [
            {
                "sensor_id": "55555555-5555-4555-8555-555555555555",
                "first_observed_out_at": "2026-09-17T10:15:00Z",
                "last_observed_out_at": "2026-09-17T10:25:00Z",
                "estimated_out_of_range_seconds": 600.0,
                "unknown_duration_seconds": 0.0,
                "sample_count": 45,
                "observed_min_c": 4.5,
                "observed_max_c": 11.2,
                "censored_start": False,
                "censored_end": False,
                "coverage_status": "complete",
                "evidence_ids": [ev_id],
            }
        ],
        "outcome": "hypothesis_supported",
        "primary_hypothesis": "door_exposure",
        "hypotheses": [
            {
                "hypothesis": "door_exposure",
                "assessment": "supported",
                "supporting_evidence_ids": [ev_id],
                "conflicting_evidence_ids": [],
                "missing_evidence": [],
                "explanation": "Door open event precedes temperature excursion.",
            }
        ],
        "next_checks": [
            {
                "code": "inspect_door",
                "reason": "Verify door seal integrity.",
                "related_evidence_ids": [ev_id],
            }
        ],
        "limitations": ["Simulated environment"],
        "verification": {
            "status": "passed",
            "errors": [],
            "warnings": [],
        },
        "generation_mode": "bedrock",
        "review_required": True,
        "evidence": [ev_ref],
    }
    payload.update(overrides)
    return payload


def test_report_valid_construction_with_evidence() -> None:
    data = _make_valid_report()
    report = Report.model_validate(data)
    assert report.outcome == Outcome.hypothesis_supported
    assert len(report.evidence) == 1
    dumped = report.model_dump(mode="json")
    assert "evidence" in dumped
    assert dumped["evidence"][0]["evidence_id"] == "00000000-0000-4000-8000-000000000001"


def test_report_rejects_evidence_with_mismatched_snapshot_id() -> None:
    data = _make_valid_report()
    data["evidence"][0]["snapshot_id"] = "99999999-9999-4999-8999-999999999999"
    with pytest.raises(ValidationError, match="does not match report snapshot_id"):
        Report.model_validate(data)


def test_report_rejects_hypothesis_with_unknown_supporting_evidence_id() -> None:
    data = _make_valid_report()
    data["hypotheses"][0]["supporting_evidence_ids"] = ["99999999-9999-4999-8999-999999999999"]
    with pytest.raises(ValidationError, match="cites unknown supporting evidence_id"):
        Report.model_validate(data)


def test_report_rejects_hypothesis_with_unknown_conflicting_evidence_id() -> None:
    data = _make_valid_report()
    data["hypotheses"][0]["conflicting_evidence_ids"] = ["99999999-9999-4999-8999-999999999999"]
    with pytest.raises(ValidationError, match="cites unknown conflicting evidence_id"):
        Report.model_validate(data)


def test_report_rejects_next_check_with_unknown_related_evidence_id() -> None:
    data = _make_valid_report()
    data["next_checks"][0]["related_evidence_ids"] = ["99999999-9999-4999-8999-999999999999"]
    with pytest.raises(ValidationError, match="cites unknown related evidence_id"):
        Report.model_validate(data)


def test_report_rejects_measurement_with_unknown_evidence_id() -> None:
    data = _make_valid_report()
    data["measurements"][0]["evidence_ids"] = ["99999999-9999-4999-8999-999999999999"]
    with pytest.raises(ValidationError, match="cites unknown evidence_id"):
        Report.model_validate(data)


def test_evidence_ref_model() -> None:
    ev = EvidenceRef(
        evidence_id="00000000-0000-4000-8000-000000000001",
        snapshot_id="11111111-1111-4111-8111-111111111111",
        kind=EvidenceKind.event,
        record_ids=["22222222-2222-4222-8222-222222222222"],
        summary="Door open event",
        observed_at="2026-09-17T10:10:00Z",
    )
    assert ev.kind == EvidenceKind.event
    assert ev.evidence_id == "00000000-0000-4000-8000-000000000001"


def test_hypothesis_and_verification_models() -> None:
    hyp = Hypothesis(
        hypothesis=HypothesisType.door_exposure,
        assessment="supported",
        supporting_evidence_ids=["00000000-0000-4000-8000-000000000001"],
        conflicting_evidence_ids=[],
        missing_evidence=[],
        explanation="Door open event correlates with excursion.",
    )
    assert hyp.hypothesis == HypothesisType.door_exposure

    ver = Verification(
        status=VerificationStatus.passed,
        errors=[],
        warnings=[],
    )
    assert ver.status == VerificationStatus.passed


def test_report_summary_model() -> None:
    rs = ReportSummary(
        outcome=Outcome.hypothesis_supported,
        primary_hypothesis=HypothesisType.door_exposure,
        review_required=True,
    )
    assert rs.outcome == Outcome.hypothesis_supported
    assert rs.primary_hypothesis == HypothesisType.door_exposure


def test_report_generation_mode() -> None:
    data = _make_valid_report(generation_mode=GenerationMode.bedrock.value)
    report = Report.model_validate(data)
    assert report.generation_mode == GenerationMode.bedrock


def test_report_rejects_measurement_with_unknown_evidence_id_when_evidence_list_is_empty() -> None:
    """Unconditional citation validation:
    Citations must be rejected even when evidence list is empty.
    """
    data = _make_valid_report()
    data["evidence"] = []
    data["hypotheses"] = []
    data["next_checks"] = []
    data["measurements"][0]["evidence_ids"] = ["99999999-9999-4999-8999-999999999999"]
    with pytest.raises(ValidationError, match="cites unknown evidence_id"):
        Report.model_validate(data)


def test_run_model_dump_omits_none_label() -> None:
    """Run serialization must omit label if None, but preserve string if present."""
    run_no_label = Run(
        run_id="00000000-0000-4000-8000-000000000001",
        status=RunStatus.queued,
        stage=PublicStage.preparing,
        created_at=datetime(2026, 9, 17, 10, 45, 5, tzinfo=UTC),
        shipment_id="00000000-0000-4000-8000-000000000002",
        snapshot_id="00000000-0000-4000-8000-000000000003",
        label=None,
    )
    dumped = run_no_label.model_dump(mode="json")
    assert "label" not in dumped
    assert "snapshot_ref" not in dumped

    run_with_label = Run(
        run_id="00000000-0000-4000-8000-000000000001",
        status=RunStatus.queued,
        stage=PublicStage.preparing,
        created_at=datetime(2026, 9, 17, 10, 45, 5, tzinfo=UTC),
        shipment_id="00000000-0000-4000-8000-000000000002",
        snapshot_id="00000000-0000-4000-8000-000000000003",
        label="door_seal_failure",
    )
    dumped_with = run_with_label.model_dump(mode="json")
    assert dumped_with.get("label") == "door_seal_failure"


def test_run_summary_model_dump_omits_none_label() -> None:
    """RunSummary serialization must omit label if None."""
    summary = RunSummary(
        run_id="00000000-0000-4000-8000-000000000001",
        status=RunStatus.queued,
        stage=PublicStage.preparing,
        created_at=datetime(2026, 9, 17, 10, 45, 5, tzinfo=UTC),
        label=None,
    )
    dumped = summary.model_dump(mode="json")
    assert "label" not in dumped


def test_evidence_ref_model_dump_omits_none_fields() -> None:
    """EvidenceRef serialization must omit optional fields when None."""
    ev = EvidenceRef(
        evidence_id="00000000-0000-4000-8000-000000000001",
        snapshot_id="00000000-0000-4000-8000-000000000002",
        kind=EvidenceKind.reading,
        summary="Reading summary",
        record_ids=["00000000-0000-4000-8000-000000000003"],
        observed_at=None,
        interval=None,
        method_version=None,
    )
    dumped = ev.model_dump(mode="json")
    assert "observed_at" not in dumped
    assert "interval" not in dumped
    assert "method_version" not in dumped
    assert dumped["evidence_id"] == "00000000-0000-4000-8000-000000000001"


def test_report_model_dump_omits_none_fields_in_evidence() -> None:
    """Report.model_dump must omit optional None fields in evidence items."""
    data = _make_valid_report()
    data["evidence"][0]["observed_at"] = None
    data["evidence"][0]["interval"] = None
    data["evidence"][0]["method_version"] = None
    report = Report.model_validate(data)
    dumped = report.model_dump(mode="json")
    ev_item = dumped["evidence"][0]
    assert "observed_at" not in ev_item
    assert "interval" not in ev_item
    assert "method_version" not in ev_item


def test_preparation_record_validation() -> None:
    """PreparationRecord validates scenario_id, seed, base_timestamp, snapshot/shipment UUIDs."""
    prep = PreparationRecord(
        run_id="00000000-0000-4000-8000-000000000001",
        scenario_id="door_exposure",
        seed=42,
        base_timestamp=datetime(2026, 9, 17, 8, 0, tzinfo=UTC),
        snapshot_id="00000000-0000-4000-8000-000000000002",
        shipment_id="00000000-0000-4000-8000-000000000003",
    )
    assert prep.seed == 42
    assert prep.scenario_id == "door_exposure"
    assert prep.base_timestamp.isoformat() == "2026-09-17T08:00:00+00:00"


def test_run_persists_seed_and_base_timestamp_excluded_from_wire() -> None:
    """Run stores seed and base_timestamp for crash recovery, but excludes them from wire JSON."""
    base_time = datetime(2026, 9, 17, 8, 0, tzinfo=UTC)
    run = Run(
        run_id="00000000-0000-4000-8000-000000000001",
        status=RunStatus.queued,
        stage=PublicStage.preparing,
        created_at=base_time,
        shipment_id="00000000-0000-4000-8000-000000000002",
        snapshot_id="00000000-0000-4000-8000-000000000003",
        scenario_id="door_exposure",
        seed=101,
        generator_base_time=base_time,
    )
    assert run.seed == 101
    assert run.base_timestamp == base_time
    assert run.generator_base_timestamp == base_time
    assert run.generator_base_time == base_time

    # Wire JSON dump must NOT include seed or base_timestamp
    dumped = run.model_dump(mode="json")
    assert "seed" not in dumped
    assert "base_timestamp" not in dumped
    assert "generator_base_time" not in dumped
    assert "generator_base_timestamp" not in dumped

    # Conversion to PreparationRecord
    prep = run.to_preparation_record()
    assert prep is not None
    assert prep.seed == 101
    assert prep.scenario_id == "door_exposure"
    assert prep.base_timestamp == base_time



