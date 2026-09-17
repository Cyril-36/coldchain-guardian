"""Tests for the deterministic excursion detector."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

import pytest

from coldchain.contracts.enums import (
    CoverageStatus,
    EventType,
    EvidenceKind,
    GenerationMode,
    HypothesisType,
    Outcome,
    SensorRole,
    VerificationStatus,
)
from coldchain.contracts.schemas import (
    Event,
    Policy,
    Reading,
    Report,
    Sensor,
    Snapshot,
    Verification,
    snapshot_sha256,
)
from coldchain.core.detector import (
    DETECTOR_VERSION,
    build_detection_result,
    build_measurement_evidence,
    check_multiple_windows,
    detect_excursions,
    detect_excursions_with_evidence,
    is_excursion_detected,
    validate_snapshot,
)
from coldchain.simulator import generate_snapshot

BASE_TS = datetime(2026, 9, 17, 10, 0, 0, tzinfo=UTC)
POLICY = Policy(
    policy_id="pol-default",
    policy_version="1.0",
    min_c=2.0,
    max_c=8.0,
    expected_interval_seconds=60.0,
    max_gap_seconds=120.0,
)

_TEST_NAMESPACE = UUID("00000000-0000-4000-8000-000000000000")


def _uid(name: str) -> str:
    """Deterministic UUID string helper for test inputs."""
    return str(uuid5(_TEST_NAMESPACE, name))


def _ts(seconds: int) -> datetime:
    return BASE_TS + timedelta(seconds=seconds)


def _reading(event_name: str, sensor_name: str, seconds: int, temp: float) -> Reading:
    return Reading(
        event_id=_uid(event_name),
        sensor_id=_uid(sensor_name),
        observed_at=_ts(seconds),
        temperature_c=temp,
    )


def _event(event_name: str, seconds: int, event_type: EventType, value: str) -> Event:
    return Event(
        event_id=_uid(event_name),
        observed_at=_ts(seconds),
        event_type=event_type,
        value=value,
        source="simulated",
    )


def make_snapshot(
    readings: list[Reading],
    sensors: list[Sensor] | None = None,
    events: list[Event] | None = None,
    cutoff_seconds: int = 600,
    policy: Policy | None = None,
) -> Snapshot:
    """Build a valid Snapshot for detector tests."""
    if sensors is None:
        sensors = [
            Sensor(sensor_id=_uid("s-ref"), placement="front_air", role=SensorRole.reference),
        ]
    return Snapshot(
        snapshot_id=_uid("snap-1"),
        shipment_id=_uid("ship-1"),
        schema_version="1.0",
        source="simulated",
        cutoff_at=_ts(cutoff_seconds),
        policy=policy or POLICY,
        sensors=sensors,
        readings=readings,
        events=events or [],
    )


# ── Version and Public Contract Tests ───────────────────────────────────────


def test_detector_version():
    """DETECTOR_VERSION is declared and follows semver."""
    assert DETECTOR_VERSION == "1.0.0"


# ── Threshold Boundary Tests ────────────────────────────────────────────────


def test_exact_boundary_in_range():
    """2.0 and 8.0 are strictly IN range for policy min=2.0, max=8.0."""
    readings = [
        _reading("r-0", "s-ref", 0, 2.0),
        _reading("r-1", "s-ref", 60, 8.0),
        _reading("r-2", "s-ref", 120, 5.0),
    ]
    snap = make_snapshot(readings)
    measurements = detect_excursions(snap, POLICY)

    assert len(measurements) == 1
    m = measurements[0]
    assert is_excursion_detected(m) is False
    assert m.estimated_out_of_range_seconds == 0.0
    assert m.first_observed_out_at is None
    assert m.last_observed_out_at is None
    assert m.observed_min_c == 2.0
    assert m.observed_max_c == 8.0


def test_boundary_strictness_high_and_low():
    """Values strictly beyond 2.0 and 8.0 trigger excursion detection."""
    # Just above max (8.01)
    high_snap = make_snapshot([
        _reading("rh-0", "s-ref", 0, 5.0),
        _reading("rh-1", "s-ref", 60, 8.01),
        _reading("rh-2", "s-ref", 120, 5.0),
    ])
    high_m = detect_excursions(high_snap, POLICY)[0]
    assert is_excursion_detected(high_m) is True
    assert high_m.estimated_out_of_range_seconds > 0.0
    assert high_m.first_observed_out_at == _ts(60)

    # Just below min (1.99)
    low_snap = make_snapshot([
        _reading("rl-0", "s-ref", 0, 5.0),
        _reading("rl-1", "s-ref", 60, 1.99),
        _reading("rl-2", "s-ref", 120, 5.0),
    ])
    low_m = detect_excursions(low_snap, POLICY)[0]
    assert is_excursion_detected(low_m) is True
    assert low_m.estimated_out_of_range_seconds > 0.0
    assert low_m.first_observed_out_at == _ts(60)


# ── Interpolation Tests ────────────────────────────────────────────────────


def test_90_second_interpolation():
    """VERIFICATION.md: 5,9,9,5°C at 60-second intervals yields 90.0s above 8°C.

    [0, 60]:   5→9, crosses 8 at t=45.  Out portion: 60 - 45 = 15s
    [60, 120]: 9→9, both above 8.       Out portion: 60s
    [120, 180]:9→5, crosses 8 at t=135. Out portion: 135 - 120 = 15s
    Total: 15 + 60 + 15 = 90.0s
    """
    sensors = [
        Sensor(sensor_id=_uid("s-ref"), placement="front_air", role=SensorRole.reference),
        Sensor(sensor_id=_uid("s-cmp"), placement="rear_air", role=SensorRole.comparison),
    ]
    readings = [
        # Reference sensor — excursion
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-1", "s-ref", 60, 9.0),
        _reading("r-2", "s-ref", 120, 9.0),
        _reading("r-3", "s-ref", 180, 5.0),
        # Comparison sensor — normal
        _reading("c-0", "s-cmp", 0, 5.0),
        _reading("c-1", "s-cmp", 60, 5.0),
        _reading("c-2", "s-cmp", 120, 5.0),
        _reading("c-3", "s-cmp", 180, 5.0),
    ]
    snap = make_snapshot(readings, sensors=sensors, cutoff_seconds=300)
    measurements = detect_excursions(snap, POLICY)

    ref_m = next(m for m in measurements if m.sensor_id == _uid("s-ref"))
    cmp_m = next(m for m in measurements if m.sensor_id == _uid("s-cmp"))

    assert is_excursion_detected(ref_m) is True
    assert ref_m.estimated_out_of_range_seconds == 90.0
    assert ref_m.first_observed_out_at == _ts(60)
    assert ref_m.last_observed_out_at == _ts(120)
    assert ref_m.sample_count == 4
    assert ref_m.observed_min_c == 5.0
    assert ref_m.observed_max_c == 9.0

    assert is_excursion_detected(cmp_m) is False
    assert cmp_m.estimated_out_of_range_seconds == 0.0


def test_low_temperature_excursion():
    """VERIFICATION.md: Below-min excursions detected independently.

    Symmetric low test: 5, 1, 1, 5°C at 60s intervals against min=2.0°C.
    [0, 60]:   5→1, crosses 2 at t=45.  Out portion: 15s
    [60, 120]: 1→1, both below 2.       Out portion: 60s
    [120, 180]:1→5, crosses 2 at t=135. Out portion: 15s
    Total: 15 + 60 + 15 = 90.0s below 2.0°C
    """
    readings = [
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-1", "s-ref", 60, 1.0),
        _reading("r-2", "s-ref", 120, 1.0),
        _reading("r-3", "s-ref", 180, 5.0),
    ]
    snap = make_snapshot(readings)
    measurements = detect_excursions(snap, POLICY)

    m = measurements[0]
    assert is_excursion_detected(m) is True
    assert m.estimated_out_of_range_seconds == 90.0
    assert m.observed_min_c == 1.0
    assert m.first_observed_out_at == _ts(60)
    assert m.last_observed_out_at == _ts(120)


def test_straddling_both_thresholds():
    """An interval transitioning from below min (0°C) to above max (10°C)."""
    # min=2, max=8, dt=60s. Crossing 2 at t=12s (12s below min).
    # Crossing 8 at t=48s (12s above max).
    # Out portion: 12 + 12 = 24.0s.
    readings = [
        _reading("r-0", "s-ref", 0, 0.0),
        _reading("r-1", "s-ref", 60, 10.0),
    ]
    snap = make_snapshot(readings)
    measurements = detect_excursions(snap, POLICY)
    m = measurements[0]
    assert is_excursion_detected(m) is True
    assert m.estimated_out_of_range_seconds == 24.0
    assert m.censored_start is True
    assert m.censored_end is True


# ── Gap Handling Tests ──────────────────────────────────────────────────────


def test_gap_no_interpolation():
    """VERIFICATION.md: Gaps > max_gap_seconds are NOT interpolated and yield partial coverage."""
    readings = [
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-1", "s-ref", 300, 5.0),  # 300s gap > 120s max_gap
    ]
    snap = make_snapshot(readings)
    measurements = detect_excursions(snap, POLICY)

    m = measurements[0]
    assert m.unknown_duration_seconds == 300.0
    assert m.coverage_status == CoverageStatus.partial
    assert m.estimated_out_of_range_seconds == 0.0
    assert is_excursion_detected(m) is False


# ── Censoring Tests ─────────────────────────────────────────────────────────


def test_censored_start():
    """First reading out of range → censored_start=True."""
    readings = [
        _reading("r-0", "s-ref", 0, 10.0),
        _reading("r-1", "s-ref", 60, 5.0),
    ]
    snap = make_snapshot(readings)
    measurements = detect_excursions(snap, POLICY)

    m = measurements[0]
    assert m.censored_start is True
    assert m.censored_end is False


def test_censored_end():
    """Last reading out of range → censored_end=True."""
    readings = [
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-1", "s-ref", 60, 10.0),
    ]
    snap = make_snapshot(readings)
    measurements = detect_excursions(snap, POLICY)

    m = measurements[0]
    assert m.censored_start is False
    assert m.censored_end is True


def test_censored_both():
    """Both first and last readings out of range → both flags True."""
    readings = [
        _reading("r-0", "s-ref", 0, 10.0),
        _reading("r-1", "s-ref", 60, 10.0),
    ]
    snap = make_snapshot(readings)
    measurements = detect_excursions(snap, POLICY)

    m = measurements[0]
    assert m.censored_start is True
    assert m.censored_end is True


# ── Deduplication and Sorting Tests ─────────────────────────────────────────


def test_duplicate_readings_deduplicated():
    """Identical duplicate readings are deduplicated cleanly."""
    readings = [
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-0", "s-ref", 0, 5.0),  # identical duplicate
        _reading("r-1", "s-ref", 60, 5.0),
    ]
    snap = make_snapshot(readings)
    clean = validate_snapshot(snap)

    assert len(clean.readings) == 2
    assert clean.readings[0].event_id == _uid("r-0")
    assert clean.readings[1].event_id == _uid("r-1")


def test_conflicting_duplicate_reading_rejected():
    """Conflicting duplicate readings with same event_id are rejected."""
    readings = [
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-0", "s-ref", 0, 7.0),  # conflicting temp
        _reading("r-1", "s-ref", 60, 5.0),
    ]
    # 1. Pydantic Snapshot constructor rejects conflicting duplicates
    with pytest.raises(Exception, match="Conflicting duplicate event_id"):
        make_snapshot(readings)

    # 2. validate_snapshot directly checks and raises ValueError
    constructed_snap = Snapshot.model_construct(
        snapshot_id=_uid("snap-1"),
        shipment_id=_uid("ship-1"),
        schema_version="1.0",
        source="simulated",
        cutoff_at=_ts(600),
        policy=POLICY,
        sensors=[Sensor(sensor_id=_uid("s-ref"), placement="front_air", role=SensorRole.reference)],
        readings=readings,
        events=[],
    )
    with pytest.raises(ValueError, match="Conflicting duplicate reading"):
        validate_snapshot(constructed_snap)


def test_duplicate_events_deduplicated():
    """Identical duplicate events are deduplicated cleanly."""
    events = [
        _event("e-0", 0, EventType.door_state, "open"),
        _event("e-0", 0, EventType.door_state, "open"),  # identical duplicate
        _event("e-1", 60, EventType.door_state, "closed"),
    ]
    snap = make_snapshot(
        readings=[_reading("r-0", "s-ref", 0, 5.0)],
        events=events,
    )
    clean = validate_snapshot(snap)

    assert len(clean.events) == 2
    assert clean.events[0].event_id == _uid("e-0")
    assert clean.events[1].event_id == _uid("e-1")


def test_conflicting_duplicate_event_rejected():
    """Conflicting duplicate events with same event_id are rejected."""
    events = [
        _event("e-0", 0, EventType.door_state, "open"),
        _event("e-0", 0, EventType.door_state, "closed"),  # conflicting value
    ]
    # 1. Pydantic Snapshot constructor rejects conflicting duplicate events
    with pytest.raises(Exception, match="Conflicting duplicate event_id"):
        make_snapshot(
            readings=[_reading("r-0", "s-ref", 0, 5.0)],
            events=events,
        )

    # 2. validate_snapshot directly checks and raises ValueError
    constructed_snap = Snapshot.model_construct(
        snapshot_id=_uid("snap-1"),
        shipment_id=_uid("ship-1"),
        schema_version="1.0",
        source="simulated",
        cutoff_at=_ts(600),
        policy=POLICY,
        sensors=[Sensor(sensor_id=_uid("s-ref"), placement="front_air", role=SensorRole.reference)],
        readings=[_reading("r-0", "s-ref", 0, 5.0)],
        events=events,
    )
    with pytest.raises(ValueError, match="Conflicting duplicate event"):
        validate_snapshot(constructed_snap)


def test_out_of_order_sorting():
    """Readings and events given out of chronological order are sorted."""
    readings = [
        _reading("r-1", "s-ref", 60, 5.0),
        _reading("r-0", "s-ref", 0, 5.0),
    ]
    events = [
        _event("e-1", 60, EventType.door_state, "closed"),
        _event("e-0", 0, EventType.door_state, "open"),
    ]
    snap = make_snapshot(readings=readings, events=events)
    clean = validate_snapshot(snap)

    assert clean.readings[0].event_id == _uid("r-0")
    assert clean.readings[1].event_id == _uid("r-1")
    assert clean.events[0].event_id == _uid("e-0")
    assert clean.events[1].event_id == _uid("e-1")


# ── Normal Control Tests ────────────────────────────────────────────────────


def test_no_excursion_normal_control():
    """All readings within range yields has_excursion=False, needs_review=False."""
    readings = [
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-1", "s-ref", 60, 6.0),
        _reading("r-2", "s-ref", 120, 4.0),
    ]
    snap = make_snapshot(readings)
    res = build_detection_result(snap)

    assert res.has_excursion is False
    assert res.needs_review is False
    assert res.reason is None
    assert len(res.measurements) == 1
    assert is_excursion_detected(res.measurements[0]) is False
    assert res.measurements[0].estimated_out_of_range_seconds == 0.0


# ── Secondary Sensor Anomaly Tests ──────────────────────────────────────────


def test_comparison_only_secondary_sensor_anomaly():
    """Secondary-only excursion: sensor disagreement not lost because reference is normal."""
    sensors = [
        Sensor(sensor_id=_uid("s-ref"), placement="front_air", role=SensorRole.reference),
        Sensor(sensor_id=_uid("s-cmp"), placement="rear_air", role=SensorRole.comparison),
    ]
    readings = [
        # Reference — normal
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-1", "s-ref", 60, 5.0),
        # Comparison — excursion
        _reading("c-0", "s-cmp", 0, 5.0),
        _reading("c-1", "s-cmp", 60, 12.0),
    ]
    snap = make_snapshot(readings, sensors=sensors)
    res = build_detection_result(snap)

    assert len(res.measurements) == 2
    ref_m = next(m for m in res.measurements if m.sensor_id == _uid("s-ref"))
    cmp_m = next(m for m in res.measurements if m.sensor_id == _uid("s-cmp"))

    assert is_excursion_detected(ref_m) is False
    assert is_excursion_detected(cmp_m) is True
    assert cmp_m.estimated_out_of_range_seconds > 0.0

    # Critical requirement: entire detection result marks excursion and requires review
    assert res.has_excursion is True
    assert res.needs_review is True
    assert res.reason is None


# ── Multiple Windows Tests ──────────────────────────────────────────────────


def test_multiple_windows_gap_during_excursion():
    """A sensor with an excursion and an unknown duration gap indicates multiple windows."""
    readings = [
        _reading("r-0", "s-ref", 0, 10.0),     # out of range
        _reading("r-1", "s-ref", 300, 10.0),   # gap > 120s, still out of range
    ]
    snap = make_snapshot(readings)
    res = build_detection_result(snap)

    assert res.has_excursion is True
    assert res.needs_review is True
    assert res.reason == "multiple_windows_unsupported"
    assert check_multiple_windows(res.measurements, snap) is True


def test_multiple_windows_disjoint_spikes():
    """Sensor with multiple separate excursion windows separated by normal readings."""
    readings = [
        # First excursion window
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-1", "s-ref", 60, 10.0),
        _reading("r-2", "s-ref", 120, 5.0),
        # Normal interval
        _reading("r-3", "s-ref", 180, 5.0),
        # Second excursion window
        _reading("r-4", "s-ref", 240, 11.0),
        _reading("r-5", "s-ref", 300, 5.0),
    ]
    snap = make_snapshot(readings)
    res = build_detection_result(snap)

    assert res.has_excursion is True
    assert res.needs_review is True
    assert res.reason == "multiple_windows_unsupported"
    assert check_multiple_windows(res.measurements, snap) is True


def test_excursion_followed_by_unrelated_normal_gap():
    """An excursion followed by an unrelated gap between normal readings preserves

    partial coverage without triggering multiple_windows_unsupported.
    """
    readings = [
        # One excursion window (5 -> 9 -> 9 -> 5 yields 90s above 8°C)
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-1", "s-ref", 60, 9.0),
        _reading("r-2", "s-ref", 120, 9.0),
        _reading("r-3", "s-ref", 180, 5.0),
        # Normal reading before gap
        _reading("r-4", "s-ref", 240, 5.0),
        # 280-second normal-to-normal gap (520 - 240 = 280s > 120s max_gap)
        _reading("r-5", "s-ref", 520, 5.0),
        _reading("r-6", "s-ref", 580, 5.0),
    ]
    snap = make_snapshot(readings, cutoff_seconds=600)
    res = build_detection_result(snap)

    # Excursion is detected
    assert res.has_excursion is True
    # Requires review because an excursion occurred
    assert res.needs_review is True
    # Must NOT claim multiple windows
    assert res.reason is None
    assert check_multiple_windows(res.measurements, snap) is False

    # Partial coverage is preserved on the measurement
    m = res.measurements[0]
    assert m.estimated_out_of_range_seconds == 90.0
    assert m.unknown_duration_seconds == 280.0
    assert m.coverage_status == CoverageStatus.partial


# ── Sensor Coverage and Evidence Determinism Tests ──────────────────────────


def test_sensor_with_no_readings():
    """A configured comparison sensor with no readings triggers needs_review."""
    sensors = [
        Sensor(sensor_id=_uid("s-ref"), placement="front_air", role=SensorRole.reference),
        Sensor(sensor_id=_uid("s-cmp"), placement="rear_air", role=SensorRole.comparison),
    ]
    # Only supply readings for s-ref (normal readings)
    readings = [
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-1", "s-ref", 60, 5.0),
    ]
    snap = make_snapshot(readings, sensors=sensors)
    res = build_detection_result(snap)

    cmp_m = next(m for m in res.measurements if m.sensor_id == _uid("s-cmp"))
    assert cmp_m.sample_count == 0
    assert cmp_m.coverage_status == CoverageStatus.insufficient
    assert is_excursion_detected(cmp_m) is False

    # Missing data must not be treated as a normal control
    assert res.has_excursion is False
    assert res.needs_review is True
    assert res.reason == "insufficient_coverage"
    assert cmp_m.evidence_ids == [
        str(uuid5(UUID(snap.snapshot_id), f"measurement:{cmp_m.sensor_id}"))
    ]


def test_sensor_with_single_reading():
    """A reference sensor with only one reading has insufficient coverage.

    Triggers needs_review without treating normal reading as a normal control.
    """
    readings = [
        _reading("r-0", "s-ref", 0, 5.0),
    ]
    snap = make_snapshot(readings)
    res = build_detection_result(snap)

    m = res.measurements[0]
    assert m.sample_count == 1
    assert m.coverage_status == CoverageStatus.insufficient

    # Normal single reading cannot be treated as a normal control
    assert res.has_excursion is False
    assert res.needs_review is True
    assert res.reason == "insufficient_coverage"


def test_build_measurement_evidence_and_report_construction():
    """Measurements cite evidence IDs matching produced EvidenceRef items.

    A canonical Report constructed from these measurements and evidence
    passes schema and provenance validation.
    """
    door_file = Path(__file__).parents[3] / "contracts" / "examples" / "door-snapshot.json"
    with open(door_file) as f:
        data = json.load(f)

    snapshot = Snapshot.model_validate(data)
    result = build_detection_result(snapshot)

    assert len(result.evidence) == len(result.measurements)
    ev_map = {e.evidence_id: e for e in result.evidence}

    for m in result.measurements:
        assert len(m.evidence_ids) == 1
        eid = m.evidence_ids[0]
        assert eid in ev_map
        ev = ev_map[eid]
        assert ev.snapshot_id == snapshot.snapshot_id
        assert ev.kind == EvidenceKind.derived_metric
        assert ev.method_version == DETECTOR_VERSION
        assert len(ev.record_ids) == m.sample_count
        assert ev.interval is not None
        assert ev.interval.start_at == m.first_observed_out_at
        assert ev.interval.end_at == m.last_observed_out_at

    # Verify build_measurement_evidence produces identical evidence
    direct_evidence = build_measurement_evidence(snapshot, result.measurements)
    assert direct_evidence == result.evidence

    # Construct a valid canonical Report
    report = Report(
        report_id=str(uuid5(_TEST_NAMESPACE, "report-1")),
        run_id=str(uuid5(_TEST_NAMESPACE, "run-1")),
        snapshot_id=snapshot.snapshot_id,
        snapshot_sha256=snapshot_sha256(snapshot),
        detector_version=DETECTOR_VERSION,
        prompt_version="1.0.0",
        created_at=datetime.now(UTC),
        cutoff_at=snapshot.cutoff_at,
        measurements=result.measurements,
        outcome=Outcome.hypothesis_supported,
        primary_hypothesis=HypothesisType.door_exposure,
        hypotheses=[],
        next_checks=[],
        limitations=[],
        verification=Verification(status=VerificationStatus.passed),
        generation_mode=GenerationMode.deterministic_only,
        review_required=result.needs_review,
        evidence=result.evidence,
    )
    assert report.report_id is not None
    assert len(report.evidence) == 2

    # Verify detect_excursions_with_evidence helper
    measurements, evidence = detect_excursions_with_evidence(snapshot, snapshot.policy)
    assert len(measurements) == 2
    assert len(evidence) == 2

    # Tampering check: omitting evidence causes provenance validation to reject Report
    with pytest.raises(ValueError, match="cites unknown evidence_id"):
        Report(
            report_id=str(uuid5(_TEST_NAMESPACE, "report-2")),
            run_id=str(uuid5(_TEST_NAMESPACE, "run-2")),
            snapshot_id=snapshot.snapshot_id,
            snapshot_sha256=snapshot_sha256(snapshot),
            detector_version=DETECTOR_VERSION,
            created_at=datetime.now(UTC),
            cutoff_at=snapshot.cutoff_at,
            measurements=result.measurements,
            outcome=Outcome.hypothesis_supported,
            verification=Verification(status=VerificationStatus.passed),
            generation_mode=GenerationMode.deterministic_only,
            review_required=result.needs_review,
            evidence=[],  # Missing evidence triggers provenance validation error
        )


def test_deterministic_evidence_ids():
    """Evidence IDs in measurements are deterministic UUIDs based on snapshot and sensor IDs."""
    readings = [
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-1", "s-ref", 60, 9.0),
    ]
    snap = make_snapshot(readings)
    m1 = detect_excursions(snap, POLICY)
    m2 = detect_excursions(snap, POLICY)

    assert len(m1[0].evidence_ids) == 1
    assert len(m2[0].evidence_ids) == 1
    assert m1[0].evidence_ids[0] == m2[0].evidence_ids[0]

    # Verify evidence_id is a valid UUID
    parsed = UUID(m1[0].evidence_ids[0])
    assert parsed.version == 5


# ── Real Integration Tests (Door Snapshot & Simulator Scenarios) ────────────


def test_door_snapshot_example_file():
    """Run detector on contracts/examples/door-snapshot.json and verify results."""
    door_file = Path(__file__).parents[3] / "contracts" / "examples" / "door-snapshot.json"
    with open(door_file) as f:
        data = json.load(f)

    snapshot = Snapshot.model_validate(data)
    result = build_detection_result(snapshot)

    assert result.has_excursion is True
    assert result.needs_review is True
    assert result.reason is None  # Single window, not multiple windows
    assert len(result.measurements) == 2

    for m in result.measurements:
        assert m.sample_count == 46
        assert m.coverage_status == CoverageStatus.complete
        assert is_excursion_detected(m) is True
        assert m.estimated_out_of_range_seconds > 500.0
        assert m.observed_max_c is not None and m.observed_max_c > 8.0
        assert m.first_observed_out_at is not None
        assert m.last_observed_out_at is not None


@pytest.mark.parametrize(
    "scenario_id,expected_has_excursion,expected_needs_review",
    [
        ("normal_control", False, False),
        ("door_exposure", True, True),
        ("refrigeration_problem", True, True),
        ("sensor_disagreement", True, True),
        ("ambiguous_incident", True, True),
    ],
)
def test_simulator_scenarios_integration(
    scenario_id: str,
    expected_has_excursion: bool,
    expected_needs_review: bool,
):
    """Detector accurately identifies excursions across all 5 synthetic scenarios."""
    base_ts = datetime(2026, 9, 17, 8, 0, 0, tzinfo=UTC)
    raw = generate_snapshot(scenario_id, seed=100, base_timestamp=base_ts)
    snapshot = Snapshot.model_validate(raw)

    result = build_detection_result(snapshot)

    assert result.has_excursion is expected_has_excursion
    assert result.needs_review is expected_needs_review
    assert result.reason is None  # Normal scenarios are single continuous windows
    assert len(result.measurements) == 2
