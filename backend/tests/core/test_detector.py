"""Tests for the deterministic excursion detector."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from coldchain.contracts.enums import CoverageStatus, SensorRole
from coldchain.contracts.schemas import (
    Policy,
    Reading,
    Sensor,
    Snapshot,
)
from coldchain.core.detector import (
    build_detection_result,
    check_multiple_windows,
    detect_excursions,
    validate_snapshot,
)

BASE_TS = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

POLICY = Policy(
    policy_id="pol-1",
    policy_version="1.0",
    min_c=2.0,
    max_c=8.0,
    expected_interval_seconds=60.0,
    max_gap_seconds=120.0,
)


def _ts(seconds: int) -> datetime:
    return BASE_TS + timedelta(seconds=seconds)


def make_snapshot(
    readings: list[Reading],
    sensors: list[Sensor] | None = None,
    cutoff_seconds: int = 600,
) -> Snapshot:
    """Build a minimal valid Snapshot for detector tests."""
    if sensors is None:
        sensors = [
            Sensor(sensor_id="s-ref", placement="center", role=SensorRole.reference),
        ]
    return Snapshot(
        snapshot_id=str(uuid.uuid4()),
        shipment_id=str(uuid.uuid4()),
        cutoff_at=_ts(cutoff_seconds),
        policy=POLICY,
        sensors=sensors,
        readings=readings,
        events=[],
    )


def _reading(event_id: str, sensor_id: str, seconds: int, temp: float) -> Reading:
    return Reading(
        event_id=event_id,
        sensor_id=sensor_id,
        observed_at=_ts(seconds),
        temperature_c=temp,
    )


# ── Boundary tests ──────────────────────────────────────────────────────────


def test_exact_boundary_in_range():
    """2.0 and 8.0 are IN range for policy min=2, max=8."""
    readings = [
        _reading("r-0", "s-ref", 0, 2.0),
        _reading("r-1", "s-ref", 60, 8.0),
    ]
    snap = make_snapshot(readings)
    measurements = detect_excursions(snap, POLICY)

    assert len(measurements) == 1
    m = measurements[0]
    assert m.excursion_detected is False
    assert m.estimated_out_of_range_seconds == 0.0


# ── Interpolation tests ────────────────────────────────────────────────────


def test_90_second_interpolation():
    """Readings at t=0:5°C, t=60:9°C, t=120:9°C, t=180:5°C → 90.0s out.

    [0,60]:   5→9, crosses 8 at t=45.  Out portion: 60-45 = 15s
    [60,120]: 9→9, both above 8.       Out portion: 60s
    [120,180]:9→5, crosses 8 at t=135. Out portion: 135-120 = 15s
    Total: 15 + 60 + 15 = 90.0s
    """
    sensors = [
        Sensor(sensor_id="s-ref", placement="center", role=SensorRole.reference),
        Sensor(sensor_id="s-cmp", placement="door", role=SensorRole.comparison),
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

    ref_m = next(m for m in measurements if m.sensor_id == "s-ref")
    assert ref_m.excursion_detected is True
    assert ref_m.estimated_out_of_range_seconds == 90.0


def test_low_temperature_excursion():
    """Readings below min_c are detected as excursion."""
    readings = [
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-1", "s-ref", 60, 1.0),
        _reading("r-2", "s-ref", 120, 5.0),
    ]
    snap = make_snapshot(readings)
    measurements = detect_excursions(snap, POLICY)

    m = measurements[0]
    assert m.excursion_detected is True
    assert m.estimated_out_of_range_seconds > 0
    assert m.observed_min_c == 1.0


# ── Gap handling ────────────────────────────────────────────────────────────


def test_gap_no_interpolation():
    """Readings with gap > max_gap_seconds → unknown_duration_seconds > 0."""
    readings = [
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-1", "s-ref", 300, 5.0),  # 300s gap > 120s max_gap
    ]
    snap = make_snapshot(readings)
    measurements = detect_excursions(snap, POLICY)

    m = measurements[0]
    assert m.unknown_duration_seconds > 0
    assert m.coverage_status == CoverageStatus.partial
    # No interpolation across the gap, so no out-of-range time
    assert m.estimated_out_of_range_seconds == 0.0


# ── Censoring tests ────────────────────────────────────────────────────────


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


# ── Deduplication tests ─────────────────────────────────────────────────────


def test_duplicate_deduplication():
    """Identical duplicate readings are deduplicated."""
    readings = [
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-0", "s-ref", 0, 5.0),  # identical duplicate
        _reading("r-1", "s-ref", 60, 5.0),
    ]
    snap = make_snapshot(readings)
    clean = validate_snapshot(snap)

    assert len(clean.readings) == 2


def test_conflicting_duplicate_rejection():
    """Different readings with same event_id raise ValueError."""
    readings = [
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-0", "s-ref", 0, 7.0),  # same event_id, different temp
        _reading("r-1", "s-ref", 60, 5.0),
    ]
    snap = make_snapshot(readings)

    with pytest.raises(ValueError, match="Conflicting duplicate"):
        validate_snapshot(snap)


# ── Ordering tests ──────────────────────────────────────────────────────────


def test_out_of_order_sorting():
    """Readings given out of order are sorted by time."""
    readings = [
        _reading("r-1", "s-ref", 60, 5.0),
        _reading("r-0", "s-ref", 0, 5.0),
    ]
    snap = make_snapshot(readings)
    clean = validate_snapshot(snap)

    assert clean.readings[0].event_id == "r-0"
    assert clean.readings[1].event_id == "r-1"


# ── No excursion test ───────────────────────────────────────────────────────


def test_no_excursion():
    """All readings in range → excursion_detected=False."""
    readings = [
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-1", "s-ref", 60, 6.0),
        _reading("r-2", "s-ref", 120, 4.0),
    ]
    snap = make_snapshot(readings)
    measurements = detect_excursions(snap, POLICY)

    m = measurements[0]
    assert m.excursion_detected is False
    assert m.estimated_out_of_range_seconds == 0.0


# ── Comparison sensor tests ────────────────────────────────────────────────


def test_comparison_only_excursion():
    """Reference in range, comparison excursion → both returned, comparison shows excursion."""
    sensors = [
        Sensor(sensor_id="s-ref", placement="center", role=SensorRole.reference),
        Sensor(sensor_id="s-cmp", placement="door", role=SensorRole.comparison),
    ]
    readings = [
        # Reference — in range
        _reading("r-0", "s-ref", 0, 5.0),
        _reading("r-1", "s-ref", 60, 5.0),
        # Comparison — excursion
        _reading("c-0", "s-cmp", 0, 5.0),
        _reading("c-1", "s-cmp", 60, 12.0),
    ]
    snap = make_snapshot(readings, sensors=sensors)
    measurements = detect_excursions(snap, POLICY)

    assert len(measurements) == 2
    ref_m = next(m for m in measurements if m.sensor_id == "s-ref")
    cmp_m = next(m for m in measurements if m.sensor_id == "s-cmp")

    assert ref_m.excursion_detected is False
    assert cmp_m.excursion_detected is True


# ── Multiple windows test ──────────────────────────────────────────────────


def test_multiple_windows_detection():
    """Sensor with gap + excursion → check_multiple_windows returns True."""
    sensors = [
        Sensor(sensor_id="s-ref", placement="center", role=SensorRole.reference),
    ]
    readings = [
        _reading("r-0", "s-ref", 0, 10.0),     # out of range
        _reading("r-1", "s-ref", 300, 10.0),    # gap > 120s, still out of range
    ]
    snap = make_snapshot(readings, sensors=sensors)
    measurements = detect_excursions(snap, POLICY)

    m = measurements[0]
    assert m.excursion_detected is True
    assert m.unknown_duration_seconds > 0

    assert check_multiple_windows(measurements) is True
