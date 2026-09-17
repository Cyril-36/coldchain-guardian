"""Tests for ColdChain Guardian Pydantic v2 schemas."""

import math
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from coldchain.contracts.enums import (
    EventType,
    GenerationMode,
    Outcome,
    ReviewDecision,
    SensorRole,
    VerificationStatus,
)
from coldchain.contracts.schemas import (
    Event,
    Policy,
    QueueMessage,
    Reading,
    Report,
    Review,
    Sensor,
    SensorMeasurement,
    Snapshot,
    Verification,
)

BASE_TS = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _ts(minutes: int) -> datetime:
    return BASE_TS + timedelta(minutes=minutes)


def make_snapshot(**overrides) -> Snapshot:
    """Build a minimal valid Snapshot with sensible defaults."""
    defaults = {
        "snapshot_id": str(uuid.uuid4()),
        "shipment_id": str(uuid.uuid4()),
        "schema_version": "1.0",
        "source": "simulated",
        "cutoff_at": _ts(10),
        "policy": Policy(
            policy_id="pol-1",
            policy_version="1.0",
            min_c=2.0,
            max_c=8.0,
            expected_interval_seconds=60.0,
            max_gap_seconds=120.0,
        ),
        "sensors": [
            Sensor(sensor_id="s-ref", placement="center", role=SensorRole.reference),
        ],
        "readings": [
            Reading(
                event_id="r-0",
                sensor_id="s-ref",
                observed_at=BASE_TS,
                temperature_c=5.0,
            ),
        ],
        "events": [],
    }
    defaults.update(overrides)
    return Snapshot(**defaults)


# ── Schema validation tests ─────────────────────────────────────────────────


def test_snapshot_rejects_unknown_fields():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        make_snapshot(unknown_field="boom")


def test_snapshot_requires_one_reference_sensor():
    # Zero reference sensors
    with pytest.raises(ValidationError, match="Exactly one reference sensor required"):
        make_snapshot(
            sensors=[
                Sensor(sensor_id="s-cmp", placement="door", role=SensorRole.comparison),
            ],
            readings=[
                Reading(
                    event_id="r-0",
                    sensor_id="s-cmp",
                    observed_at=BASE_TS,
                    temperature_c=5.0,
                ),
            ],
        )

    # Two reference sensors
    with pytest.raises(ValidationError, match="Exactly one reference sensor required"):
        make_snapshot(
            sensors=[
                Sensor(sensor_id="s-ref-1", placement="center", role=SensorRole.reference),
                Sensor(sensor_id="s-ref-2", placement="back", role=SensorRole.reference),
            ],
            readings=[
                Reading(
                    event_id="r-0",
                    sensor_id="s-ref-1",
                    observed_at=BASE_TS,
                    temperature_c=5.0,
                ),
            ],
        )


def test_reading_rejects_nan():
    with pytest.raises(ValidationError, match="NaN/infinity not allowed"):
        Reading(
            event_id="r-nan",
            sensor_id="s-ref",
            observed_at=BASE_TS,
            temperature_c=float("nan"),
        )


def test_reading_rejects_infinity():
    with pytest.raises(ValidationError, match="NaN/infinity not allowed"):
        Reading(
            event_id="r-inf",
            sensor_id="s-ref",
            observed_at=BASE_TS,
            temperature_c=float("inf"),
        )

    with pytest.raises(ValidationError, match="NaN/infinity not allowed"):
        Reading(
            event_id="r-neginf",
            sensor_id="s-ref",
            observed_at=BASE_TS,
            temperature_c=float("-inf"),
        )


def test_snapshot_max_readings_limit():
    readings = [
        Reading(
            event_id=f"r-{i}",
            sensor_id="s-ref",
            observed_at=_ts(0),
            temperature_c=5.0,
        )
        for i in range(2001)
    ]
    with pytest.raises(ValidationError, match="At most 2000 readings allowed"):
        make_snapshot(readings=readings, cutoff_at=_ts(10))


def test_snapshot_rejects_past_cutoff_reading():
    with pytest.raises(ValidationError, match="past cutoff"):
        make_snapshot(
            cutoff_at=BASE_TS,
            readings=[
                Reading(
                    event_id="r-future",
                    sensor_id="s-ref",
                    observed_at=_ts(1),
                    temperature_c=5.0,
                ),
            ],
        )


def test_snapshot_rejects_unknown_sensor_id():
    with pytest.raises(ValidationError, match="unknown sensor_id"):
        make_snapshot(
            readings=[
                Reading(
                    event_id="r-0",
                    sensor_id="s-nonexistent",
                    observed_at=BASE_TS,
                    temperature_c=5.0,
                ),
            ],
        )


def test_report_valid_construction():
    report = Report(
        report_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        snapshot_id=str(uuid.uuid4()),
        snapshot_sha256="a" * 64,
        detector_version="1.0.0",
        created_at=BASE_TS,
        cutoff_at=BASE_TS,
        measurements=[
            SensorMeasurement(
                sensor_id="s-ref",
                role=SensorRole.reference,
                excursion_detected=False,
                sample_count=1,
            ),
        ],
        outcome=Outcome.no_excursion,
        verification=Verification(status=VerificationStatus.passed),
        generation_mode=GenerationMode.deterministic_only,
        review_required=False,
    )
    assert report.schema_version == "1.0"
    assert report.outcome == Outcome.no_excursion
    assert report.review_required is False
    assert len(report.measurements) == 1


def test_review_note_max_length():
    with pytest.raises(ValidationError, match="Note maximum 1000 characters"):
        Review(
            run_id=str(uuid.uuid4()),
            actor_sub="user-1",
            report_id=str(uuid.uuid4()),
            decision=ReviewDecision.acknowledged,
            note="x" * 1001,
            created_at=BASE_TS,
        )

    # Exactly 1000 is fine
    review = Review(
        run_id=str(uuid.uuid4()),
        actor_sub="user-1",
        report_id=str(uuid.uuid4()),
        decision=ReviewDecision.acknowledged,
        note="x" * 1000,
        created_at=BASE_TS,
    )
    assert len(review.note) == 1000


def test_queue_message_schema_version():
    msg = QueueMessage(
        run_id=str(uuid.uuid4()),
        snapshot_id=str(uuid.uuid4()),
    )
    assert msg.schema_version == "1.0"

    with pytest.raises(ValidationError):
        QueueMessage(
            run_id=str(uuid.uuid4()),
            snapshot_id=str(uuid.uuid4()),
            schema_version="2.0",
        )
