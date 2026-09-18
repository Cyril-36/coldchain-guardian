"""Read-only evidence tools bound to a snapshot.

Tools return structured data and frozen EvidenceRef objects. They are snapshot-bound:
the investigator cannot supply arbitrary S3 keys, query other runs, or reference
records outside the authorized snapshot.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from coldchain.contracts.enums import (
    EventType,
    EvidenceKind,
    SensorRole,
)
from coldchain.contracts.schemas import (
    Event,
    EvidenceInterval,
    EvidenceRef,
    Reading,
    SensorMeasurement,
    Snapshot,
)
from coldchain.core.detector import (
    build_measurement_evidence,
    detect_excursions,
    validate_snapshot,
)

TOOLS_VERSION = "1.0.0"

DISAGREEMENT_THRESHOLD_C = 1.5
MAX_ALIGNMENT_OFFSET_SECONDS = 30.0
MAX_RETURNED_ALIGNED_READINGS = 10
MAX_RETURNED_EVENTS = 10


class ToolContext:
    """Holds snapshot, measurements, and evidence registry for a single run.

    Enforces strict snapshot isolation, cutoff boundaries, and deterministic caching.
    """

    def __init__(
        self,
        snapshot: Snapshot,
        measurements: list[SensorMeasurement] | None = None,
        run_id: str | None = None,
    ) -> None:
        # Validate snapshot_id is valid UUID
        try:
            uuid.UUID(snapshot.snapshot_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"Invalid snapshot_id UUID: {snapshot.snapshot_id}") from exc

        # Verify cutoff invariants
        for r in snapshot.readings:
            if r.observed_at > snapshot.cutoff_at:
                raise ValueError(
                    f"Reading {r.event_id} observed_at {r.observed_at} "
                    f"occurs after cutoff {snapshot.cutoff_at}"
                )
        for e in snapshot.events:
            if e.observed_at > snapshot.cutoff_at:
                raise ValueError(
                    f"Event {e.event_id} observed_at {e.observed_at} "
                    f"occurs after cutoff {snapshot.cutoff_at}"
                )

        clean = validate_snapshot(snapshot)
        self.snapshot = clean
        self.run_id = run_id

        # Deterministically compute expected measurements from core detector
        expected_measurements = detect_excursions(clean, clean.policy)
        expected_by_sensor = {m.sensor_id: m for m in expected_measurements}
        configured_sensor_ids = {s.sensor_id for s in clean.sensors}

        # If caller provides measurements, strictly validate them against the detector result
        if measurements is not None:
            supplied_sensor_ids = [m.sensor_id for m in measurements]
            for m in measurements:
                if m.sensor_id not in configured_sensor_ids:
                    raise ValueError(
                        f"Supplied measurement references unknown sensor: {m.sensor_id}"
                    )
            if set(supplied_sensor_ids) != configured_sensor_ids or len(supplied_sensor_ids) != len(
                configured_sensor_ids
            ):
                raise ValueError(
                    f"Supplied measurements do not match snapshot sensors: {supplied_sensor_ids}"
                )
            for m in measurements:
                exp = expected_by_sensor[m.sensor_id]
                if (
                    m.estimated_out_of_range_seconds != exp.estimated_out_of_range_seconds
                    or m.unknown_duration_seconds != exp.unknown_duration_seconds
                    or m.observed_min_c != exp.observed_min_c
                    or m.observed_max_c != exp.observed_max_c
                    or m.censored_start != exp.censored_start
                    or m.censored_end != exp.censored_end
                    or m.coverage_status != exp.coverage_status
                    or m.sample_count != exp.sample_count
                    or m.first_observed_out_at != exp.first_observed_out_at
                    or m.last_observed_out_at != exp.last_observed_out_at
                ):
                    raise ValueError(
                        f"Supplied measurement for sensor {m.sensor_id} does not match "
                        f"deterministic detector result: "
                        f"estimated_out_of_range_seconds={m.estimated_out_of_range_seconds} "
                        f"(expected {exp.estimated_out_of_range_seconds})."
                    )
            self.measurements = list(expected_measurements)
        else:
            self.measurements = list(expected_measurements)

        self._valid_record_ids: set[str] = {
            r.event_id for r in clean.readings
        } | {e.event_id for e in clean.events}

        self._evidence_registry: dict[str, EvidenceRef] = {}
        self._cache: dict[str, dict[str, Any]] = {}

    def register_evidence(self, ref: EvidenceRef) -> None:
        """Register an evidence item, enforcing provenance, record ID, and cutoff checks."""
        if ref.snapshot_id != self.snapshot.snapshot_id:
            raise ValueError(
                f"Evidence {ref.evidence_id} snapshot_id ({ref.snapshot_id}) does not "
                f"match context snapshot_id ({self.snapshot.snapshot_id})"
            )

        # Validate that all record_ids belong to the snapshot
        for rec_id in ref.record_ids:
            if rec_id not in self._valid_record_ids:
                raise ValueError(
                    f"Evidence {ref.evidence_id} references unknown record_id: {rec_id}"
                )

        # Cutoff checks
        if ref.observed_at is not None and ref.observed_at > self.snapshot.cutoff_at:
            raise ValueError(
                f"Evidence {ref.evidence_id} observed_at {ref.observed_at} "
                f"exceeds cutoff {self.snapshot.cutoff_at}"
            )
        if ref.interval is not None and ref.interval.end_at > self.snapshot.cutoff_at:
            raise ValueError(
                f"Evidence {ref.evidence_id} interval end {ref.interval.end_at} "
                f"exceeds cutoff {self.snapshot.cutoff_at}"
            )

        self._evidence_registry[ref.evidence_id] = ref

    def get_evidence(self, evidence_id: str) -> EvidenceRef | None:
        """Retrieve a registered evidence reference by ID."""
        return self._evidence_registry.get(evidence_id)

    def all_evidence(self) -> list[EvidenceRef]:
        """Return all registered evidence references sorted by evidence_id."""
        return [self._evidence_registry[eid] for eid in sorted(self._evidence_registry.keys())]

    def all_evidence_ids(self) -> set[str]:
        """Return set of all registered evidence IDs."""
        return set(self._evidence_registry.keys())


# ── Tool Implementations ───────────────────────────────────────────────────


def get_excursion_summary(ctx: ToolContext) -> dict[str, Any]:
    """Return verified per-sensor metrics, coverage, and deterministic evidence."""
    if "excursion_summary" in ctx._cache:
        return ctx._cache["excursion_summary"]

    # Register measurement evidence from detector
    detector_refs = build_measurement_evidence(
        ctx.snapshot, ctx.measurements, policy=ctx.snapshot.policy
    )
    for ref in detector_refs:
        ctx.register_evidence(ref)

    sensor_summaries: list[dict[str, Any]] = []
    sensors_by_id = {s.sensor_id: s for s in ctx.snapshot.sensors}

    for m in ctx.measurements:
        sensor = sensors_by_id.get(m.sensor_id)
        if sensor is None:
            raise ValueError(f"Measurement references unknown sensor: {m.sensor_id}")
        role_str = sensor.role.value
        placement_str = sensor.placement
        ev_id = m.evidence_ids[0] if m.evidence_ids else str(
            uuid.uuid5(uuid.UUID(ctx.snapshot.snapshot_id), f"measurement:{m.sensor_id}")
        )

        sensor_summaries.append(
            {
                "sensor_id": m.sensor_id,
                "role": role_str,
                "placement": placement_str,
                "excursion_detected": (
                    m.estimated_out_of_range_seconds > 0.0 or m.first_observed_out_at is not None
                ),
                "estimated_out_of_range_seconds": m.estimated_out_of_range_seconds,
                "unknown_duration_seconds": m.unknown_duration_seconds,
                "observed_min_c": m.observed_min_c,
                "observed_max_c": m.observed_max_c,
                "censored_start": m.censored_start,
                "censored_end": m.censored_end,
                "coverage_status": m.coverage_status.value,
                "sample_count": m.sample_count,
                "first_observed_out_at": (
                    m.first_observed_out_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if m.first_observed_out_at
                    else None
                ),
                "last_observed_out_at": (
                    m.last_observed_out_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if m.last_observed_out_at
                    else None
                ),
                "evidence_id": ev_id,
            }
        )

    has_excursion = any(s["excursion_detected"] for s in sensor_summaries)
    result = {
        "sensors": sensor_summaries,
        "has_excursion": has_excursion,
        "evidence_ids": [s["evidence_id"] for s in sensor_summaries],
    }
    ctx._cache["excursion_summary"] = result
    return result


def get_sensor_comparison(ctx: ToolContext) -> dict[str, Any]:
    """Return aligned readings and disagreement evidence between sensors.

    Does NOT interpolate or align readings across gaps exceeding max alignment tolerance.
    Where observations cannot support comparison, returns insufficient/unaligned evidence
    rather than a confirmed disagreement. Evidence intervals describe only readings actually used.
    """
    if "sensor_comparison" in ctx._cache:
        return ctx._cache["sensor_comparison"]

    ref_sensor = next(
        (s for s in ctx.snapshot.sensors if s.role == SensorRole.reference),
        None,
    )
    if ref_sensor is None:
        raise ValueError("Snapshot does not contain a configured reference sensor")

    cmp_sensors = [s for s in ctx.snapshot.sensors if s.role == SensorRole.comparison]

    readings_by_sensor: dict[str, list[Reading]] = {}
    for r in ctx.snapshot.readings:
        readings_by_sensor.setdefault(r.sensor_id, []).append(r)

    ref_readings = sorted(
        readings_by_sensor.get(ref_sensor.sensor_id, []),
        key=lambda r: (r.observed_at, r.event_id),
    )

    comparisons: list[dict[str, Any]] = []
    max_alignment_seconds = min(
        MAX_ALIGNMENT_OFFSET_SECONDS,
        ctx.snapshot.policy.expected_interval_seconds / 2.0,
    )

    for cmp_sensor in cmp_sensors:
        cmp_readings = sorted(
            readings_by_sensor.get(cmp_sensor.sensor_id, []),
            key=lambda r: (r.observed_at, r.event_id),
        )

        ev_id = str(
            uuid.uuid5(
                uuid.UUID(ctx.snapshot.snapshot_id),
                f"comparison:{ref_sensor.sensor_id}:{cmp_sensor.sensor_id}",
            )
        )

        if not cmp_readings or not ref_readings:
            # Explicitly record missing comparison data
            summary = (
                f"Sensor comparison {ref_sensor.sensor_id} vs {cmp_sensor.sensor_id}: "
                f"insufficient data (ref={len(ref_readings)} samples, "
                f"cmp={len(cmp_readings)} samples)."
            )
            ref = EvidenceRef(
                evidence_id=ev_id,
                snapshot_id=ctx.snapshot.snapshot_id,
                kind=EvidenceKind.derived_metric,
                record_ids=[],
                interval=None,
                summary=summary,
                method_version=TOOLS_VERSION,
            )
            ctx.register_evidence(ref)
            comparisons.append(
                {
                    "reference_sensor_id": ref_sensor.sensor_id,
                    "comparison_sensor_id": cmp_sensor.sensor_id,
                    "aligned_readings": [],
                    "aligned_count": 0,
                    "is_truncated": False,
                    "max_difference_c": None,
                    "disagreement_detected": False,
                    "missing_comparison_data": True,
                    "unaligned_gaps": False,
                    "evidence_id": ev_id,
                }
            )
            continue

        # Align readings strictly within defensible temporal alignment window
        aligned: list[dict[str, Any]] = []
        used_ref_ids: set[str] = set()
        used_cmp_ids: set[str] = set()
        has_unaligned = False

        for cr in cmp_readings:
            candidates = [
                rr
                for rr in ref_readings
                if rr.event_id not in used_ref_ids
                and abs((rr.observed_at - cr.observed_at).total_seconds()) <= max_alignment_seconds
            ]
            if not candidates:
                has_unaligned = True
                continue

            nearest_ref = min(
                candidates,
                key=lambda rr: abs((rr.observed_at - cr.observed_at).total_seconds()),
            )
            used_ref_ids.add(nearest_ref.event_id)
            used_cmp_ids.add(cr.event_id)
            diff = round(cr.temperature_c - nearest_ref.temperature_c, 2)
            offset = round(abs((nearest_ref.observed_at - cr.observed_at).total_seconds()), 1)
            aligned.append(
                {
                    "time": cr.observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "reference_c": nearest_ref.temperature_c,
                    "comparison_c": cr.temperature_c,
                    "difference_c": diff,
                    "offset_seconds": offset,
                    "ref_event_id": nearest_ref.event_id,
                    "cmp_event_id": cr.event_id,
                }
            )

        if len(used_ref_ids) < len(ref_readings):
            has_unaligned = True

        if not aligned:
            summary = (
                f"Sensor comparison {ref_sensor.sensor_id} vs {cmp_sensor.sensor_id}: "
                f"insufficient aligned data "
                f"(no readings within {max_alignment_seconds}s tolerance)."
            )
            ref = EvidenceRef(
                evidence_id=ev_id,
                snapshot_id=ctx.snapshot.snapshot_id,
                kind=EvidenceKind.derived_metric,
                record_ids=[],
                interval=None,
                summary=summary,
                method_version=TOOLS_VERSION,
            )
            ctx.register_evidence(ref)
            comparisons.append(
                {
                    "reference_sensor_id": ref_sensor.sensor_id,
                    "comparison_sensor_id": cmp_sensor.sensor_id,
                    "aligned_readings": [],
                    "aligned_count": 0,
                    "is_truncated": False,
                    "max_difference_c": None,
                    "disagreement_detected": False,
                    "missing_comparison_data": True,
                    "unaligned_gaps": True,
                    "evidence_id": ev_id,
                }
            )
            continue

        max_diff_c = max(abs(a["difference_c"]) for a in aligned)
        disagreement_detected = max_diff_c > DISAGREEMENT_THRESHOLD_C

        # Evidence interval describes ONLY the readings actually used
        used_readings = [r for r in ref_readings if r.event_id in used_ref_ids] + [
            r for r in cmp_readings if r.event_id in used_cmp_ids
        ]
        sorted_used_times = sorted(r.observed_at for r in used_readings)
        interval = EvidenceInterval(start_at=sorted_used_times[0], end_at=sorted_used_times[-1])
        record_ids = sorted(list(used_ref_ids | used_cmp_ids))

        summary = (
            f"Sensor comparison {ref_sensor.sensor_id} vs {cmp_sensor.sensor_id}: "
            f"max difference {round(max_diff_c, 2)}°C across {len(aligned)} aligned samples"
        )
        if disagreement_detected:
            summary += f" (disagreement exceeds {DISAGREEMENT_THRESHOLD_C}°C threshold)"
        if has_unaligned:
            summary += " (some readings unaligned due to timing offset or gaps)"
        summary += "."

        ref = EvidenceRef(
            evidence_id=ev_id,
            snapshot_id=ctx.snapshot.snapshot_id,
            kind=EvidenceKind.derived_metric,
            record_ids=record_ids,
            interval=interval,
            summary=summary,
            method_version=TOOLS_VERSION,
        )
        ctx.register_evidence(ref)

        # Bound model-facing aligned_readings size while preserving count and max difference
        is_truncated = len(aligned) > MAX_RETURNED_ALIGNED_READINGS
        if is_truncated:
            max_item = max(aligned, key=lambda a: abs(a["difference_c"]))
            bounded = aligned[:5]
            if max_item not in bounded and max_item not in aligned[-4:]:
                bounded.append(max_item)
                bounded.extend(aligned[-4:])
            else:
                bounded.extend(aligned[-5:])
        else:
            bounded = aligned

        comparisons.append(
            {
                "reference_sensor_id": ref_sensor.sensor_id,
                "comparison_sensor_id": cmp_sensor.sensor_id,
                "aligned_readings": bounded,
                "aligned_count": len(aligned),
                "is_truncated": is_truncated,
                "max_difference_c": round(max_diff_c, 2),
                "disagreement_detected": disagreement_detected,
                "missing_comparison_data": False,
                "unaligned_gaps": has_unaligned,
                "evidence_id": ev_id,
            }
        )

    result = {"comparisons": comparisons, "count": len(comparisons)}
    ctx._cache["sensor_comparison"] = result
    return result


def _get_events_by_type(
    ctx: ToolContext,
    event_type: EventType,
    cache_key: str,
    event_label: str,
) -> dict[str, Any]:
    """Helper for door/refrigeration/vehicle events.

    Explicitly marks missing data rather than hallucinating default states.
    Bounds returned model-facing events while preserving total count and evidence IDs.
    """
    if cache_key in ctx._cache:
        return ctx._cache[cache_key]

    events = [e for e in ctx.snapshot.events if e.event_type == event_type]
    events.sort(key=lambda e: (e.observed_at, e.event_id))

    if not events:
        # Never convert missing into 'closed' or 'running'
        result = {
            "events": [],
            "count": 0,
            "is_truncated": False,
            "is_missing": True,
            "status": f"missing_{event_label}_events",
            "summary": f"No {event_label.replace('_', ' ')} events observed in snapshot.",
            "evidence_ids": [],
        }
        ctx._cache[cache_key] = result
        return result

    result_events: list[dict[str, Any]] = []
    for e in events:
        ev_id = str(
            uuid.uuid5(uuid.UUID(ctx.snapshot.snapshot_id), f"event:{e.event_id}")
        )
        time_str = e.observed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        ref = EvidenceRef(
            evidence_id=ev_id,
            snapshot_id=ctx.snapshot.snapshot_id,
            kind=EvidenceKind.event,
            record_ids=[e.event_id],
            observed_at=e.observed_at,
            summary=f"{event_type.value}: {e.value} at {time_str}",
        )
        ctx.register_evidence(ref)
        result_events.append(
            {
                "event_id": e.event_id,
                "observed_at": e.observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "value": e.value,
                "source": e.source,
                "evidence_id": ev_id,
            }
        )

    bounded_events = result_events[:MAX_RETURNED_EVENTS]
    is_truncated = len(result_events) > MAX_RETURNED_EVENTS

    result = {
        "events": bounded_events,
        "count": len(result_events),
        "is_truncated": is_truncated,
        "is_missing": False,
        "status": "observed",
        "evidence_ids": [ev["evidence_id"] for ev in result_events],
    }
    ctx._cache[cache_key] = result
    return result


def _compute_door_temporal_facts(
    ctx: ToolContext,
    door_events: list[Event],
) -> dict[str, Any] | None:
    """Compute deterministic temporal facts regarding door opening and temperature excursion.

    Includes door opening before rise, overlap interval, and recovery after close.
    Describes temporal association without claiming causation.
    """
    if not door_events:
        return None

    ref_sensor = next(
        (s for s in ctx.snapshot.sensors if s.role == SensorRole.reference),
        None,
    )
    if ref_sensor is None:
        return None

    ref_measurement = next(
        (m for m in ctx.measurements if m.sensor_id == ref_sensor.sensor_id),
        None,
    )
    if ref_measurement is None:
        return None

    has_excursion = (
        ref_measurement.estimated_out_of_range_seconds > 0.0
        or ref_measurement.first_observed_out_at is not None
    )
    if not has_excursion:
        return {
            "has_excursion": False,
            "door_opened_before_rise": False,
            "lead_time_seconds": None,
            "overlap_interval": None,
            "temperature_recovered_after_close": False,
            "recovery_time_seconds": None,
            "record_ids": [],
            "method_version": TOOLS_VERSION,
            "summary": (
                "No excursion detected on reference sensor; "
                "temporal door correlation not applicable."
            ),
            "evidence_id": None,
        }

    # Reference readings
    ref_readings = [r for r in ctx.snapshot.readings if r.sensor_id == ref_sensor.sensor_id]
    ref_readings.sort(key=lambda r: (r.observed_at, r.event_id))

    policy = ctx.snapshot.policy
    out_readings = [
        r
        for r in ref_readings
        if r.temperature_c < policy.min_c or r.temperature_c > policy.max_c
    ]
    if not out_readings:
        t_rise = ref_measurement.first_observed_out_at or ref_readings[0].observed_at
        first_out_rec_id = None
        t_fall = ref_measurement.last_observed_out_at or t_rise
        last_out_rec_id = None
    else:
        first_out = out_readings[0]
        last_out = out_readings[-1]
        t_rise = first_out.observed_at
        first_out_rec_id = first_out.event_id
        t_fall = last_out.observed_at
        last_out_rec_id = last_out.event_id

    sorted_door_events = sorted(door_events, key=lambda e: (e.observed_at, e.event_id))
    open_events = [e for e in sorted_door_events if e.value == "open"]
    close_events = [e for e in sorted_door_events if e.value == "closed"]

    used_record_ids: set[str] = set()
    used_timestamps: list[datetime] = []

    if first_out_rec_id:
        used_record_ids.add(first_out_rec_id)
        used_timestamps.append(t_rise)
    if last_out_rec_id:
        used_record_ids.add(last_out_rec_id)
        used_timestamps.append(t_fall)

    # 1. Door opening before rise
    prior_open = [e for e in open_events if e.observed_at <= t_rise]
    if prior_open:
        open_event = max(prior_open, key=lambda e: e.observed_at)
        door_opened_before_rise = True
        lead_time_seconds = round((t_rise - open_event.observed_at).total_seconds(), 1)
        used_record_ids.add(open_event.event_id)
        used_timestamps.append(open_event.observed_at)
    else:
        door_opened_before_rise = False
        lead_time_seconds = None
        open_event = open_events[0] if open_events else None
        if open_event:
            used_record_ids.add(open_event.event_id)
            used_timestamps.append(open_event.observed_at)

    # 2. Overlap interval
    overlap_interval: dict[str, Any] | None = None
    close_event: Event | None = None
    if open_event:
        subsequent_closes = [e for e in close_events if e.observed_at >= open_event.observed_at]
        if subsequent_closes:
            close_event = min(subsequent_closes, key=lambda e: e.observed_at)
            door_closed_at = close_event.observed_at
            used_record_ids.add(close_event.event_id)
            used_timestamps.append(close_event.observed_at)
        else:
            door_closed_at = ctx.snapshot.cutoff_at

        overlap_start = max(open_event.observed_at, t_rise)
        overlap_end = min(door_closed_at, t_fall)
        if overlap_start <= overlap_end:
            overlap_duration = round((overlap_end - overlap_start).total_seconds(), 1)
            overlap_interval = {
                "start_at": overlap_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_at": overlap_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "duration_seconds": overlap_duration,
            }
            for r in ref_readings:
                if overlap_start <= r.observed_at <= overlap_end:
                    used_record_ids.add(r.event_id)
                    used_timestamps.append(r.observed_at)

    # 3. Recovery after close
    temperature_recovered_after_close = False
    recovery_time_seconds: float | None = None
    if close_event:
        post_close_readings = [
            r for r in ref_readings if r.observed_at >= close_event.observed_at
        ]
        recovery_reading = next(
            (r for r in post_close_readings if policy.min_c <= r.temperature_c <= policy.max_c),
            None,
        )
        if recovery_reading:
            temperature_recovered_after_close = True
            recovery_time_seconds = round(
                (recovery_reading.observed_at - close_event.observed_at).total_seconds(), 1
            )
            used_record_ids.add(recovery_reading.event_id)
            used_timestamps.append(recovery_reading.observed_at)

    # Narrative describing temporal association without claiming causation
    parts: list[str] = []
    if door_opened_before_rise:
        parts.append(
            f"Door open event observed {lead_time_seconds}s prior to "
            "temperature rise above threshold."
        )
    else:
        parts.append("Door open event was not observed prior to temperature rise.")

    if overlap_interval:
        parts.append(
            f"Door-open and excursion overlap duration was {overlap_interval['duration_seconds']}s "
            f"({overlap_interval['start_at']} to {overlap_interval['end_at']})."
        )
    else:
        parts.append("No overlap observed between open door and excursion window.")

    if temperature_recovered_after_close:
        parts.append(
            f"Temperature returned to normal range {recovery_time_seconds}s after door closed."
        )
    else:
        if close_event:
            parts.append(
                "Temperature did not recover to normal range after door closed prior to cutoff."
            )
        else:
            parts.append("No door close event observed prior to cutoff.")

    parts.append("Temporal association observed; does not establish causation.")
    summary = " ".join(parts)

    ev_id = str(uuid.uuid5(uuid.UUID(ctx.snapshot.snapshot_id), "door_temporal_facts"))
    sorted_times = sorted(used_timestamps)
    interval = (
        EvidenceInterval(start_at=sorted_times[0], end_at=sorted_times[-1])
        if sorted_times
        else None
    )

    ref = EvidenceRef(
        evidence_id=ev_id,
        snapshot_id=ctx.snapshot.snapshot_id,
        kind=EvidenceKind.derived_metric,
        record_ids=sorted(list(used_record_ids)),
        interval=interval,
        summary=summary,
        method_version=TOOLS_VERSION,
    )
    ctx.register_evidence(ref)

    return {
        "has_excursion": True,
        "door_opened_before_rise": door_opened_before_rise,
        "lead_time_seconds": lead_time_seconds,
        "overlap_interval": overlap_interval,
        "temperature_recovered_after_close": temperature_recovered_after_close,
        "recovery_time_seconds": recovery_time_seconds,
        "record_ids": sorted(list(used_record_ids)),
        "method_version": TOOLS_VERSION,
        "summary": summary,
        "evidence_id": ev_id,
    }


def get_door_events(ctx: ToolContext) -> dict[str, Any]:
    """Return door timeline and deterministic temporal facts around the excursion window.

    Never turns missing door events into 'closed'. Includes deterministic temporal facts
    (door open before rise, overlap duration, recovery after close) without claiming causation.
    """
    if "door_events" in ctx._cache:
        return ctx._cache["door_events"]

    raw = _get_events_by_type(ctx, EventType.door_state, "door_events_raw", "door")
    if raw["is_missing"]:
        raw["temporal_facts"] = None
        ctx._cache["door_events"] = raw
        return raw

    door_events_list = [e for e in ctx.snapshot.events if e.event_type == EventType.door_state]
    temporal_facts = _compute_door_temporal_facts(ctx, door_events_list)
    raw["temporal_facts"] = temporal_facts
    if (
        temporal_facts
        and temporal_facts.get("evidence_id")
        and temporal_facts["evidence_id"] not in raw["evidence_ids"]
    ):
        raw["evidence_ids"].append(temporal_facts["evidence_id"])

    ctx._cache["door_events"] = raw
    return raw


def get_refrigeration_events(ctx: ToolContext) -> dict[str, Any]:
    """Return refrigeration running/stopped/fault observations.

    Never turns missing refrigeration events into 'running'. Note that 'running'
    is a status signal and does not prove cooling effectiveness.
    """
    res = _get_events_by_type(
        ctx, EventType.refrigeration_state, "refrigeration_events", "refrigeration"
    )
    if not res["is_missing"]:
        res["has_fault_or_stopped"] = any(
            e["value"] in ("fault", "stopped") for e in res["events"]
        )
    else:
        res["has_fault_or_stopped"] = False
    return res


def get_vehicle_events(ctx: ToolContext) -> dict[str, Any]:
    """Return stationary/moving vehicle events.

    Never turns missing vehicle events into 'moving' or 'stopped'.
    """
    return _get_events_by_type(ctx, EventType.vehicle_state, "vehicle_events", "vehicle")


def get_handling_policy(ctx: ToolContext) -> dict[str, Any]:
    """Return the exact configured policy parameters and frozen policy evidence."""
    if "handling_policy" in ctx._cache:
        return ctx._cache["handling_policy"]

    p = ctx.snapshot.policy
    ev_id = str(uuid.uuid5(uuid.UUID(ctx.snapshot.snapshot_id), "policy"))
    ref = EvidenceRef(
        evidence_id=ev_id,
        snapshot_id=ctx.snapshot.snapshot_id,
        kind=EvidenceKind.policy,
        record_ids=[],
        summary=(
            f"Policy {p.policy_id} v{p.policy_version}: range [{p.min_c}, {p.max_c}] °C, "
            f"expected_interval={p.expected_interval_seconds}s, max_gap={p.max_gap_seconds}s."
        ),
    )
    ctx.register_evidence(ref)

    result = {
        "policy_id": p.policy_id,
        "policy_version": p.policy_version,
        "min_c": p.min_c,
        "max_c": p.max_c,
        "expected_interval_seconds": p.expected_interval_seconds,
        "max_gap_seconds": p.max_gap_seconds,
        "evidence_id": ev_id,
    }
    ctx._cache["handling_policy"] = result
    return result

