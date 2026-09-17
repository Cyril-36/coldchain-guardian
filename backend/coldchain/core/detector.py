"""Deterministic excursion detector for ColdChain Guardian.

Pure function of snapshot + policy.  No AWS, boto3 or model imports.
Rounding convention (CONTRACTS.md §Measurements):
  - seconds  → round(..., 1)
  - temperatures → round(..., 2)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import NamedTuple

from coldchain.contracts.enums import CoverageStatus
from coldchain.contracts.schemas import (
    Event,
    Policy,
    Reading,
    SensorMeasurement,
    Snapshot,
)

DETECTOR_VERSION = "1.0.0"


# ── Helpers ─────────────────────────────────────────────────────────────────


def _reading_sort_key(r: Reading) -> tuple[datetime, str]:
    return (r.observed_at, r.event_id)


def _ts(dt: datetime) -> float:
    """Datetime → POSIX seconds."""
    return dt.timestamp()


def _out_of_range(temp: float, min_c: float, max_c: float) -> bool:
    """Strictly below min or strictly above max is out of range."""
    return temp < min_c or temp > max_c


def _interval_out_seconds(
    t1: float,
    v1: float,
    t2: float,
    v2: float,
    min_c: float,
    max_c: float,
) -> float:
    """Return the seconds within [t1, t2] where interpolated temp is outside [min_c, max_c].

    Handles high excursions (above max_c) and low excursions (below min_c)
    independently and sums them.  Both thresholds can contribute in one
    interval if v1 and v2 straddle the entire range, though that is unusual.
    """
    dt = t2 - t1
    if dt <= 0:
        return 0.0

    out1_hi = v1 > max_c
    out2_hi = v2 > max_c
    out1_lo = v1 < min_c
    out2_lo = v2 < min_c

    out_seconds = 0.0

    # ── High excursion (above max_c) ────────────────────────────────────
    if out1_hi and out2_hi:
        out_seconds += dt
    elif out1_hi and not out2_hi:
        # crossing back in: fraction of interval above max_c
        frac = (v1 - max_c) / (v1 - v2)  # v1 > max_c >= v2
        out_seconds += dt * frac
    elif not out1_hi and out2_hi:
        # crossing out: fraction of interval above max_c
        frac = (v2 - max_c) / (v2 - v1)  # v2 > max_c >= v1
        out_seconds += dt * frac

    # ── Low excursion (below min_c) ─────────────────────────────────────
    if out1_lo and out2_lo:
        out_seconds += dt
    elif out1_lo and not out2_lo:
        frac = (min_c - v1) / (v2 - v1)  # v1 < min_c <= v2
        out_seconds += dt * frac
    elif not out1_lo and out2_lo:
        frac = (min_c - v2) / (v1 - v2)  # v2 < min_c <= v1
        out_seconds += dt * frac

    return out_seconds


def is_excursion_detected(m: SensorMeasurement) -> bool:
    """Return True if measurement indicates an excursion occurred."""
    return m.estimated_out_of_range_seconds > 0.0 or m.first_observed_out_at is not None


# ── Public API ──────────────────────────────────────────────────────────────


def validate_snapshot(snapshot: Snapshot) -> Snapshot:
    """Sort and deduplicate readings/events in *snapshot*.

    - Sort readings by (observed_at, event_id).
    - Deduplicate readings by event_id: identical duplicates are collapsed,
      conflicting duplicates raise ``ValueError``.
    - Deduplicate events by event_id: identical duplicates are collapsed,
      conflicting duplicates raise ``ValueError``.
    - Sort events by (observed_at, event_id).

    Returns a new Snapshot with cleaned data.
    """
    # ── Deduplicate readings ────────────────────────────────────────────
    seen_readings: dict[str, Reading] = {}
    for r in snapshot.readings:
        if r.event_id in seen_readings:
            existing = seen_readings[r.event_id]
            if existing != r:
                raise ValueError(
                    f"Conflicting duplicate reading for event_id {r.event_id}"
                )
            # identical duplicate — skip
        else:
            seen_readings[r.event_id] = r

    deduped_readings = sorted(seen_readings.values(), key=_reading_sort_key)

    # ── Deduplicate events ──────────────────────────────────────────────
    seen_events: dict[str, Event] = {}
    for e in snapshot.events:
        if e.event_id in seen_events:
            existing = seen_events[e.event_id]
            if existing != e:
                raise ValueError(
                    f"Conflicting duplicate event for event_id {e.event_id}"
                )
            # identical duplicate — skip
        else:
            seen_events[e.event_id] = e

    sorted_events = sorted(seen_events.values(), key=lambda e: (e.observed_at, e.event_id))

    return snapshot.model_copy(
        update={"readings": deduped_readings, "events": sorted_events},
    )


def detect_excursions(
    snapshot: Snapshot,
    policy: Policy,
) -> list[SensorMeasurement]:
    """Compute per-sensor measurements from *snapshot* against *policy*.

    For each sensor the function:
    1. Flags readings strictly outside ``[min_c, max_c]``.
    2. Uses linear interpolation between consecutive readings separated by
       at most ``max_gap_seconds`` to estimate threshold-crossing times.
    3. Records ``unknown_duration_seconds`` for gaps exceeding the limit.
    4. Marks ``censored_start`` / ``censored_end`` when the first/last
       reading is out of range.
    5. Sets deterministic evidence reference UUIDs on the measurement.
    """
    readings_by_sensor: dict[str, list[Reading]] = {}
    for r in snapshot.readings:
        readings_by_sensor.setdefault(r.sensor_id, []).append(r)

    measurements: list[SensorMeasurement] = []

    for sensor in snapshot.sensors:
        sid = sensor.sensor_id
        readings = sorted(readings_by_sensor.get(sid, []), key=_reading_sort_key)

        if not readings:
            measurements.append(
                SensorMeasurement(
                    sensor_id=sid,
                    sample_count=0,
                    coverage_status=CoverageStatus.insufficient,
                )
            )
            continue

        # ── Observed extremes ───────────────────────────────────────────
        temps = [r.temperature_c for r in readings]
        observed_min = round(min(temps), 2)
        observed_max = round(max(temps), 2)

        # ── Flag out-of-range readings ──────────────────────────────────
        flagged = [_out_of_range(r.temperature_c, policy.min_c, policy.max_c) for r in readings]

        first_out_at: datetime | None = None
        last_out_at: datetime | None = None
        for r, f in zip(readings, flagged, strict=True):
            if f:
                if first_out_at is None:
                    first_out_at = r.observed_at
                last_out_at = r.observed_at

        # ── Interpolate durations ───────────────────────────────────────
        estimated_out = 0.0
        unknown_dur = 0.0
        has_gap = False

        for i in range(len(readings) - 1):
            r1, r2 = readings[i], readings[i + 1]
            t1, t2 = _ts(r1.observed_at), _ts(r2.observed_at)
            gap = t2 - t1

            if gap > policy.max_gap_seconds:
                unknown_dur += gap
                has_gap = True
                continue

            estimated_out += _interval_out_seconds(
                t1,
                r1.temperature_c,
                t2,
                r2.temperature_c,
                policy.min_c,
                policy.max_c,
            )

        estimated_out = round(estimated_out, 1)
        unknown_dur = round(unknown_dur, 1)

        # ── Censoring ──────────────────────────────────────────────────
        censored_start = flagged[0]
        censored_end = flagged[-1]

        # ── Coverage ───────────────────────────────────────────────────
        if len(readings) <= 1:
            coverage_status = CoverageStatus.insufficient
        elif has_gap:
            coverage_status = CoverageStatus.partial
        else:
            coverage_status = CoverageStatus.complete

        # ── Evidence reference identifier ──────────────────────────────
        # Deterministic UUID5 based on snapshot and sensor IDs
        evidence_id = str(
            uuid.uuid5(uuid.UUID(snapshot.snapshot_id), f"measurement:{sid}")
        )

        measurements.append(
            SensorMeasurement(
                sensor_id=sid,
                first_observed_out_at=first_out_at,
                last_observed_out_at=last_out_at,
                estimated_out_of_range_seconds=estimated_out,
                unknown_duration_seconds=unknown_dur,
                sample_count=len(readings),
                observed_min_c=observed_min,
                observed_max_c=observed_max,
                censored_start=censored_start,
                censored_end=censored_end,
                coverage_status=coverage_status,
                evidence_ids=[evidence_id],
            )
        )

    return measurements


def check_multiple_windows(
    measurements: list[SensorMeasurement],
    snapshot: Snapshot | None = None,
) -> bool:
    """Return True if there are multiple disjoint excursion windows.

    Not supported in MVP — presence triggers needs_review with
    multiple_windows_unsupported.

    Conditions indicating multiple windows:
    1. A sensor with an excursion has an unknown duration gap (data discontinuity).
    2. A sensor readings exhibit two or more disjoint out-of-range windows
       separated by in-range readings.
    """
    # Condition 1: gap during/with excursion
    for m in measurements:
        if is_excursion_detected(m) and m.unknown_duration_seconds > 0:
            return True

    # Condition 2: multiple separate out-of-range windows
    if snapshot is not None:
        policy = snapshot.policy
        readings_by_sensor: dict[str, list[Reading]] = {}
        for r in snapshot.readings:
            readings_by_sensor.setdefault(r.sensor_id, []).append(r)

        for sensor in snapshot.sensors:
            readings = sorted(
                readings_by_sensor.get(sensor.sensor_id, []),
                key=_reading_sort_key,
            )
            if not readings:
                continue

            in_excursion = False
            window_count = 0
            for r in readings:
                out = _out_of_range(r.temperature_c, policy.min_c, policy.max_c)
                if out and not in_excursion:
                    window_count += 1
                    in_excursion = True
                elif not out and in_excursion:
                    in_excursion = False

            if window_count > 1:
                return True

    return False


# ── Convenience result type ─────────────────────────────────────────────────


class DetectionResult(NamedTuple):
    """Result of ``build_detection_result``."""

    measurements: list[SensorMeasurement]
    has_excursion: bool
    needs_review: bool
    reason: str | None


def build_detection_result(snapshot: Snapshot) -> DetectionResult:
    """Validate, detect and check for multiple windows in one call.

    Returns a ``DetectionResult`` with measurements, flags and an optional
    review reason string.
    """
    clean = validate_snapshot(snapshot)
    measurements = detect_excursions(clean, clean.policy)
    has_excursion = any(is_excursion_detected(m) for m in measurements)
    multi = check_multiple_windows(measurements, clean)

    if multi:
        return DetectionResult(
            measurements=measurements,
            has_excursion=has_excursion,
            needs_review=True,
            reason="multiple_windows_unsupported",
        )

    return DetectionResult(
        measurements=measurements,
        has_excursion=has_excursion,
        needs_review=has_excursion,
        reason=None,
    )
