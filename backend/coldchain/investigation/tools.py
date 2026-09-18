"""Read-only evidence tools bound to a snapshot.

Tools return structured data and frozen EvidenceRef objects. They are snapshot-bound:
the investigator cannot supply arbitrary S3 keys, query other runs, or reference
records outside the authorized snapshot.
"""

from __future__ import annotations

import uuid
from typing import Any

from coldchain.contracts.enums import (
    EventType,
    EvidenceKind,
    SensorRole,
)
from coldchain.contracts.schemas import (
    EvidenceInterval,
    EvidenceRef,
    Reading,
    SensorMeasurement,
    Snapshot,
)
from coldchain.core.detector import (
    build_measurement_evidence,
    detect_excursions,
)

TOOLS_VERSION = "1.0.0"

DISAGREEMENT_THRESHOLD_C = 1.5


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

        self.snapshot = snapshot
        self.run_id = run_id

        if measurements is not None:
            self.measurements = list(measurements)
        else:
            self.measurements = detect_excursions(snapshot, snapshot.policy)

        self._valid_record_ids: set[str] = {
            r.event_id for r in snapshot.readings
        } | {e.event_id for e in snapshot.events}

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
        role_str = sensor.role.value if sensor else m.role.value
        placement_str = sensor.placement if sensor else "unknown"
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

    Does NOT interpolate or align readings across gaps > max_gap_seconds.
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
                    "max_difference_c": None,
                    "disagreement_detected": False,
                    "missing_comparison_data": True,
                    "unaligned_gaps": False,
                    "evidence_id": ev_id,
                }
            )
            continue

        # Align readings without bridging gaps > max_gap_seconds
        aligned: list[dict[str, Any]] = []
        max_diff_c = 0.0
        has_unaligned_gaps = False

        for cr in cmp_readings:
            nearest_ref = min(
                ref_readings,
                key=lambda rr: abs((rr.observed_at - cr.observed_at).total_seconds()),
                default=None,
            )
            if nearest_ref is None:
                continue

            gap = abs((nearest_ref.observed_at - cr.observed_at).total_seconds())
            if gap > ctx.snapshot.policy.max_gap_seconds:
                has_unaligned_gaps = True
                continue  # Never align across wide unobserved gaps

            diff = round(cr.temperature_c - nearest_ref.temperature_c, 2)
            max_diff_c = max(max_diff_c, abs(diff))
            aligned.append(
                {
                    "time": cr.observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "reference_c": nearest_ref.temperature_c,
                    "comparison_c": cr.temperature_c,
                    "difference_c": diff,
                    "ref_event_id": nearest_ref.event_id,
                    "cmp_event_id": cr.event_id,
                }
            )

        if not aligned:
            summary = (
                f"Sensor comparison {ref_sensor.sensor_id} vs {cmp_sensor.sensor_id}: "
                f"no readings could be aligned within max gap "
                f"{ctx.snapshot.policy.max_gap_seconds}s."
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
                    "max_difference_c": None,
                    "disagreement_detected": False,
                    "missing_comparison_data": True,
                    "unaligned_gaps": True,
                    "evidence_id": ev_id,
                }
            )
            continue

        disagreement_detected = max_diff_c > DISAGREEMENT_THRESHOLD_C
        record_ids: list[str] = []
        for a in aligned:
            if a["ref_event_id"] not in record_ids:
                record_ids.append(a["ref_event_id"])
            if a["cmp_event_id"] not in record_ids:
                record_ids.append(a["cmp_event_id"])

        sorted_times = sorted([r.observed_at for r in cmp_readings + ref_readings])
        interval = EvidenceInterval(start_at=sorted_times[0], end_at=sorted_times[-1])

        summary = (
            f"Sensor comparison {ref_sensor.sensor_id} vs {cmp_sensor.sensor_id}: "
            f"max difference {round(max_diff_c, 2)}°C across {len(aligned)} aligned samples"
        )
        if disagreement_detected:
            summary += f" (disagreement exceeds {DISAGREEMENT_THRESHOLD_C}°C threshold)"
        if has_unaligned_gaps:
            summary += " (some samples unaligned due to gaps)"
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

        comparisons.append(
            {
                "reference_sensor_id": ref_sensor.sensor_id,
                "comparison_sensor_id": cmp_sensor.sensor_id,
                "aligned_readings": aligned,
                "max_difference_c": round(max_diff_c, 2),
                "disagreement_detected": disagreement_detected,
                "missing_comparison_data": False,
                "unaligned_gaps": has_unaligned_gaps,
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

    result = {
        "events": result_events,
        "count": len(result_events),
        "is_missing": False,
        "status": "observed",
        "evidence_ids": [ev["evidence_id"] for ev in result_events],
    }
    ctx._cache[cache_key] = result
    return result


def get_door_events(ctx: ToolContext) -> dict[str, Any]:
    """Return door timeline around the excursion window.

    Never turns missing door events into 'closed'.
    """
    return _get_events_by_type(ctx, EventType.door_state, "door_events", "door")


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
