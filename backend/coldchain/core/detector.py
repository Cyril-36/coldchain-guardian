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

from coldchain.contracts.enums import CoverageStatus, EvidenceKind, SensorRole
from coldchain.contracts.schemas import (
    EvidenceRef,
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


# ── Public API ──────────────────────────────────────────────────────────────


def validate_snapshot(snapshot: Snapshot) -> Snapshot:
    """Sort and deduplicate readings/events in *snapshot*.

    - Sort readings by (observed_at, event_id).
    - Deduplicate by event_id: identical duplicates are collapsed,
      conflicting duplicates raise ``ValueError``.
    - Sort events by (observed_at, event_id).

    Returns a **new** ``Snapshot`` with cleaned data (Pydantic models are
    immutable by default, so we reconstruct).
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

    # ── Sort events ─────────────────────────────────────────────────────
    sorted_events = sorted(snapshot.events, key=lambda e: (e.observed_at, e.event_id))

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
    5. Builds ``EvidenceRef`` entries for the readings used.
    """
    readings_by_sensor: dict[str, list[Reading]] = {}
    for r in snapshot.readings:
        readings_by_sensor.setdefault(r.sensor_id, []).append(r)

    sensor_map = {s.sensor_id: s for s in snapshot.sensors}
    measurements: list[SensorMeasurement] = []

    for sensor in snapshot.sensors:
        sid = sensor.sensor_id
        readings = sorted(readings_by_sensor.get(sid, []), key=_reading_sort_key)

        if not readings:
            measurements.append(
                SensorMeasurement(
                    sensor_id=sid,
                    role=sensor.role,
                    excursion_detected=False,
                    sample_count=0,
                    coverage_status=CoverageStatus.unknown,
                )
            )
            continue

        # ── Observed extremes ───────────────────────────────────────────
        temps = [r.temperature_c for r in readings]
        observed_min = round(min(temps), 2)
        observed_max = round(max(temps), 2)

        # ── Flag out-of-range readings ──────────────────────────────────
        flagged = [_out_of_range(r.temperature_c, policy.min_c, policy.max_c) for r in readings]
        any_out = any(flagged)

        first_out_at: datetime | None = None
        last_out_at: datetime | None = None
        for r, f in zip(readings, flagged):
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
        if has_gap:
            coverage_status = CoverageStatus.partial
        else:
            coverage_status = CoverageStatus.full

        # ── Evidence references ────────────────────────────────────────
        evidence_id = str(uuid.uuid4())
        evidence = EvidenceRef(
            evidence_id=evidence_id,
            snapshot_id=snapshot.snapshot_id,
            kind=EvidenceKind.reading,
            record_ids=[r.event_id for r in readings],
            interval_start=readings[0].observed_at,
            interval_end=readings[-1].observed_at,
            summary=(
                f"Sensor {sid}: {len(readings)} readings, "
                f"range [{observed_min}, {observed_max}] °C"
            ),
            method_version=DETECTOR_VERSION,
        )

        measurements.append(
            SensorMeasurement(
                sensor_id=sid,
                role=sensor.role,
                excursion_detected=any_out or estimated_out > 0,
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


def check_multiple_windows(measurements: list[SensorMeasurement]) -> bool:
    """Return ``True`` if there are multiple disjoint excursion windows.

    Not supported in MVP — presence triggers ``needs_review`` with
    ``multiple_windows_unsupported``.  Current heuristic: more than one
    sensor reports a non-contiguous excursion (both censored_start and
    censored_end are False, yet there is unknown duration in between).
    For MVP, we approximate by checking whether any sensor has both a gap
    and an excursion.
    """
    excursion_sensors = [
        m for m in measurements if m.excursion_detected and m.unknown_duration_seconds > 0
    ]
    return len(excursion_sensors) > 0


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
    has_excursion = any(m.excursion_detected for m in measurements)
    multi = check_multiple_windows(measurements)

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
