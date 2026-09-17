"""Deterministic excursion detector for ColdChain Guardian.

Pure function of snapshot + policy.  No AWS, boto3 or model imports.
Rounding convention (CONTRACTS.md §Measurements):
  - seconds  → round(..., 1)
  - temperatures → round(..., 2)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, NamedTuple

from coldchain.contracts.enums import CoverageStatus, EvidenceKind
from coldchain.contracts.schemas import (
    Event,
    EvidenceInterval,
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
    # ── Deduplicate readings and events ─────────────────────────────────
    seen: dict[str, tuple[str, dict[str, Any]]] = {}
    deduped_readings: list[Reading] = []
    for r in snapshot.readings:
        serialized_reading = r.model_dump(mode="json")
        if r.event_id in seen:
            kind, prior = seen[r.event_id]
            if kind != "reading" or prior != serialized_reading:
                raise ValueError(
                    f"Conflicting duplicate reading for event_id {r.event_id}"
                )
            # identical duplicate — skip
        else:
            seen[r.event_id] = ("reading", serialized_reading)
            deduped_readings.append(r)

    deduped_readings.sort(key=_reading_sort_key)

    deduped_events: list[Event] = []
    for e in snapshot.events:
        serialized_event = e.model_dump(mode="json")
        if e.event_id in seen:
            kind, prior = seen[e.event_id]
            if kind != "event" or prior != serialized_event:
                raise ValueError(
                    f"Conflicting duplicate event for event_id {e.event_id}"
                )
            # identical duplicate — skip
        else:
            seen[e.event_id] = ("event", serialized_event)
            deduped_events.append(e)

    deduped_events.sort(key=lambda e: (e.observed_at, e.event_id))

    return snapshot.model_copy(
        update={
            "readings": deduped_readings,
            "events": deduped_events,
        },
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
        evidence_id = str(
            uuid.uuid5(uuid.UUID(snapshot.snapshot_id), f"measurement:{sid}")
        )

        if not readings:
            measurements.append(
                SensorMeasurement(
                    sensor_id=sid,
                    sample_count=0,
                    coverage_status=CoverageStatus.insufficient,
                    evidence_ids=[evidence_id],
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


def build_measurement_evidence(
    snapshot: Snapshot,
    measurements: list[SensorMeasurement],
) -> list[EvidenceRef]:
    """Build canonical EvidenceRef items for detector measurements.

    For each measurement:
    - Sets ``kind=EvidenceKind.derived_metric``
    - Associates with the deterministic measurement evidence ID
    - Attaches sensor reading event IDs as ``record_ids``
    - Sets the out-of-range interval when an excursion was observed
    - Sets ``method_version=DETECTOR_VERSION``
    """
    sensor_map = {s.sensor_id: s for s in snapshot.sensors}
    readings_by_sensor: dict[str, list[Reading]] = {}
    for r in snapshot.readings:
        readings_by_sensor.setdefault(r.sensor_id, []).append(r)

    evidence_items: list[EvidenceRef] = []
    for m in measurements:
        sid = m.sensor_id
        sensor = sensor_map.get(sid)
        placement = sensor.placement if sensor else "unknown"
        readings = readings_by_sensor.get(sid, [])
        record_ids = [r.event_id for r in readings]

        if m.evidence_ids:
            eid = m.evidence_ids[0]
        else:
            eid = str(uuid.uuid5(uuid.UUID(snapshot.snapshot_id), f"measurement:{sid}"))

        interval: EvidenceInterval | None = None
        if m.first_observed_out_at is not None:
            interval = EvidenceInterval(
                start_at=m.first_observed_out_at,
                end_at=m.last_observed_out_at or m.first_observed_out_at,
            )

        if is_excursion_detected(m):
            summary = (
                f"Sensor {sid} ({placement}): excursion detected with estimated "
                f"{m.estimated_out_of_range_seconds}s out of range [{snapshot.policy.min_c}, "
                f"{snapshot.policy.max_c}] °C, observed range [{m.observed_min_c}, "
                f"{m.observed_max_c}] °C across {m.sample_count} samples."
            )
        elif m.sample_count == 0:
            summary = f"Sensor {sid} ({placement}): no readings observed."
        else:
            summary = (
                f"Sensor {sid} ({placement}): maintained normal temperature "
                f"range [{m.observed_min_c}, {m.observed_max_c}] °C "
                f"across {m.sample_count} samples."
            )

        evidence_items.append(
            EvidenceRef(
                evidence_id=eid,
                snapshot_id=snapshot.snapshot_id,
                kind=EvidenceKind.derived_metric,
                record_ids=record_ids,
                interval=interval,
                summary=summary,
                method_version=DETECTOR_VERSION,
            )
        )

    return evidence_items


def detect_excursions_with_evidence(
    snapshot: Snapshot,
    policy: Policy,
) -> tuple[list[SensorMeasurement], list[EvidenceRef]]:
    """Compute per-sensor measurements and matching EvidenceRef items."""
    measurements = detect_excursions(snapshot, policy)
    evidence = build_measurement_evidence(snapshot, measurements)
    return measurements, evidence


def check_multiple_windows(
    measurements: list[SensorMeasurement],
    snapshot: Snapshot | None = None,
) -> bool:
    """Return True if there are multiple disjoint excursion windows.

    Not supported in MVP — presence triggers needs_review with
    multiple_windows_unsupported.

    Conditions indicating multiple windows:
    1. A sensor readings exhibit two or more disjoint out-of-range windows
       separated by in-range readings.
    2. A gap > max_gap_seconds occurs during an excursion (i.e. at least one
       of the gap's bordering readings is out of range, or occurs while
       an excursion is in progress).
    """
    if snapshot is None:
        return False

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
        for i, r in enumerate(readings):
            out = _out_of_range(r.temperature_c, policy.min_c, policy.max_c)
            if out and not in_excursion:
                window_count += 1
                in_excursion = True
            elif not out and in_excursion:
                in_excursion = False

            # Check gap to next reading
            if i < len(readings) - 1:
                next_r = readings[i + 1]
                gap = _ts(next_r.observed_at) - _ts(r.observed_at)
                if gap > policy.max_gap_seconds:
                    next_out = _out_of_range(
                        next_r.temperature_c, policy.min_c, policy.max_c
                    )
                    # Gap touches an excursion if either endpoint is out of range
                    # or if the gap interrupted an active excursion.
                    if out or next_out or in_excursion:
                        return True

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
    evidence: list[EvidenceRef] = []


def build_detection_result(snapshot: Snapshot) -> DetectionResult:
    """Validate, detect and check for multiple windows in one call.

    Returns a ``DetectionResult`` with measurements, flags, an optional
    review reason string, and matching canonical evidence references.
    """
    clean = validate_snapshot(snapshot)
    measurements = detect_excursions(clean, clean.policy)
    has_excursion = any(is_excursion_detected(m) for m in measurements)
    multi = check_multiple_windows(measurements, clean)
    evidence = build_measurement_evidence(clean, measurements)

    if multi:
        return DetectionResult(
            measurements=measurements,
            has_excursion=has_excursion,
            needs_review=True,
            reason="multiple_windows_unsupported",
            evidence=evidence,
        )

    has_incomplete_coverage = any(
        m.coverage_status != CoverageStatus.complete for m in measurements
    )
    needs_review = has_excursion or has_incomplete_coverage

    reason: str | None = None
    if not has_excursion and has_incomplete_coverage:
        has_insufficient = any(
            m.coverage_status == CoverageStatus.insufficient for m in measurements
        )
        reason = "insufficient_coverage" if has_insufficient else "data_gap"

    return DetectionResult(
        measurements=measurements,
        has_excursion=has_excursion,
        needs_review=needs_review,
        reason=reason,
        evidence=evidence,
    )
