"""Tests validating contract example JSON fixtures against Pydantic v2 schemas."""

import json
from pathlib import Path

import pytest

from coldchain.contracts.enums import (
    Assessment,
    GenerationMode,
    HypothesisType,
    Outcome,
    PublicStage,
    RunStatus,
    SensorRole,
    VerificationStatus,
)
from coldchain.contracts.schemas import Report, Run, Snapshot

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "contracts" / "examples"


def _load_json(filename: str) -> dict:
    filepath = EXAMPLES_DIR / filename
    assert filepath.is_file(), f"Fixture file not found: {filepath}"
    with open(filepath, encoding="utf-8") as f:
        return json.load(f)


def test_door_snapshot_schema_and_behavior():
    data = _load_json("door-snapshot.json")
    snapshot = Snapshot.model_validate(data)

    assert snapshot.schema_version == "1.0"
    assert snapshot.source == "simulated"
    assert len(snapshot.sensors) == 2

    # Verify sensor roles
    ref_sensors = [s for s in snapshot.sensors if s.role == SensorRole.reference]
    cmp_sensors = [s for s in snapshot.sensors if s.role == SensorRole.comparison]
    assert len(ref_sensors) == 1
    assert len(cmp_sensors) == 1
    assert ref_sensors[0].sensor_id == "sensor-ref-001"
    assert cmp_sensors[0].sensor_id == "sensor-cmp-001"

    # Verify policy
    assert snapshot.policy.min_c == 2.0
    assert snapshot.policy.max_c == 8.0
    assert snapshot.policy.expected_interval_seconds == 60.0
    assert snapshot.policy.max_gap_seconds == 120.0

    # Verify duration and cutoff
    first_reading_time = min(r.observed_at for r in snapshot.readings)
    last_reading_time = max(r.observed_at for r in snapshot.readings)
    assert (snapshot.cutoff_at - first_reading_time).total_seconds() == 45 * 60
    assert (last_reading_time - first_reading_time).total_seconds() == 45 * 60

    # Verify door open event
    door_events = [e for e in snapshot.events if e.event_type.value == "door_state"]
    assert any(e.value == "open" for e in door_events)

    # Verify temperature rise and recovery on reference sensor
    ref_readings = [r for r in snapshot.readings if r.sensor_id == "sensor-ref-001"]
    ref_readings.sort(key=lambda r: r.observed_at)
    assert len(ref_readings) == 46

    # Minute 0-9: in range <= 8.0
    assert all(r.temperature_c <= 8.0 for r in ref_readings[:10])
    # Minute 15-24: above 8.0
    assert any(r.temperature_c > 8.0 for r in ref_readings)
    # Minute 25+: recovered <= 8.0
    assert all(r.temperature_c <= 8.0 for r in ref_readings[25:])


def test_queued_run_schema():
    data = _load_json("queued-run.json")
    run = Run.model_validate(data)

    assert run.status == RunStatus.queued
    assert run.stage == PublicStage.preparing
    assert run.snapshot_ref is not None
    assert run.report_ref is None
    assert len(run.snapshot_ref.sha256) == 64


def test_running_run_schema():
    data = _load_json("running-run.json")
    run = Run.model_validate(data)

    assert run.status == RunStatus.running
    assert run.stage == PublicStage.collecting_evidence
    assert run.snapshot_ref is not None
    assert run.report_ref is None

    # Verify stage_events show detecting completed
    detecting_events = [
        e
        for e in run.stage_events
        if e.stage == PublicStage.detecting and e.status.value == "completed"
    ]
    assert len(detecting_events) >= 1


def test_supported_report_schema():
    data = _load_json("supported-report.json")
    report = Report.model_validate(data)

    assert report.schema_version == "1.0"
    assert report.outcome == Outcome.hypothesis_supported
    assert report.primary_hypothesis == HypothesisType.door_exposure
    assert report.generation_mode == GenerationMode.bedrock
    assert report.review_required is True
    assert report.verification.status == VerificationStatus.passed
    assert len(report.snapshot_sha256) == 64

    # Verify 2 measurements
    assert len(report.measurements) == 2
    ref_measurement = next(m for m in report.measurements if m.role == SensorRole.reference)
    cmp_measurement = next(m for m in report.measurements if m.role == SensorRole.comparison)

    assert ref_measurement.excursion_detected is True
    assert pytest.approx(ref_measurement.estimated_out_of_range_seconds, rel=1e-2) == 600.0
    assert cmp_measurement.excursion_detected is False
    assert cmp_measurement.estimated_out_of_range_seconds == 0.0

    # Verify at least one hypothesis is supported
    supported_hyp = [h for h in report.hypotheses if h.assessment == Assessment.supported]
    assert len(supported_hyp) >= 1
    assert supported_hyp[0].hypothesis == HypothesisType.door_exposure


def test_unresolved_report_schema():
    data = _load_json("unresolved-report.json")
    report = Report.model_validate(data)

    assert report.schema_version == "1.0"
    assert report.outcome == Outcome.unresolved
    assert report.primary_hypothesis is None
    assert report.generation_mode == GenerationMode.bedrock
    assert len(report.hypotheses) > 0
    assert all(h.assessment == Assessment.insufficient for h in report.hypotheses)


def test_model_unavailable_report_schema():
    data = _load_json("model-unavailable-report.json")
    report = Report.model_validate(data)

    assert report.schema_version == "1.0"
    assert report.outcome == Outcome.unresolved
    assert report.generation_mode == GenerationMode.deterministic_only
    assert report.limitations == ["AI model unavailable; deterministic analysis only"]
    assert report.verification.status == VerificationStatus.passed
    assert report.primary_hypothesis is None


def test_normal_report_schema():
    data = _load_json("normal-report.json")
    report = Report.model_validate(data)

    assert report.schema_version == "1.0"
    assert report.outcome == Outcome.no_excursion
    assert report.generation_mode == GenerationMode.deterministic_only
    assert report.review_required is False
    assert len(report.hypotheses) == 0
    assert report.primary_hypothesis is None
    assert report.verification.status == VerificationStatus.passed


def test_failed_run_schema():
    data = _load_json("failed-run.json")
    run = Run.model_validate(data)

    assert run.status == RunStatus.failed
    assert run.stage == PublicStage.failed
    assert run.error == "Maximum retries exceeded"
    assert run.snapshot_ref is not None
    assert run.report_ref is None
