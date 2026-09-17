"""Comprehensive tests for canonical Pydantic v2 schemas and validation rules."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from coldchain.contracts.enums import (
    CoverageStatus,
    NextCheckCode,
    PublicStage,
    ReviewDecision,
    RunStatus,
    StageEventStatus,
)
from coldchain.contracts.schemas import (
    MAX_SNAPSHOT_EVENTS,
    MAX_SNAPSHOT_READINGS,
    ArtifactRef,
    ClaimResult,
    NextCheck,
    Policy,
    QueueMessage,
    Reading,
    Review,
    Run,
    Snapshot,
    StageEvent,
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
    assert CoverageStatus.full.value == "full"
    assert CoverageStatus.partial.value == "partial"
    assert CoverageStatus.unknown.value == "unknown"


# ── Review & Run Model Tests ────────────────────────────────────────────────


def test_review_note_length_limit() -> None:
    review = Review(
        run_id="11111111-1111-4111-8111-111111111111",
        actor_sub="operator-001",
        report_id="22222222-2222-4222-8222-222222222222",
        decision=ReviewDecision.acknowledged,
        note="a" * 1000,
        created_at="2026-09-17T12:00:00Z",
    )
    assert len(review.note) == 1000

    with pytest.raises(ValidationError, match="at most 1000 characters"):
        Review(
            run_id="11111111-1111-4111-8111-111111111111",
            actor_sub="operator-001",
            report_id="22222222-2222-4222-8222-222222222222",
            decision=ReviewDecision.acknowledged,
            note="a" * 1001,
            created_at="2026-09-17T12:00:00Z",
        )


def test_run_stage_events_bounded_at_40() -> None:
    events = [
        StageEvent(
            event_id=f"{i:08x}-0000-4000-8000-000000000000",
            stage=PublicStage.detecting,
            started_at="2026-09-17T10:00:00Z",
            status=StageEventStatus.completed,
        )
        for i in range(41)
    ]
    with pytest.raises(ValidationError, match="cannot exceed 40 items"):
        Run(
            run_id="11111111-1111-4111-8111-111111111111",
            owner_sub="user-1",
            status=RunStatus.running,
            stage=PublicStage.detecting,
            stage_events=events,
            created_at="2026-09-17T10:00:00Z",
            updated_at="2026-09-17T10:00:01Z",
        )


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
