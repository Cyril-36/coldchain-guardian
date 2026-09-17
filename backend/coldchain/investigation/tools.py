"""Read-only evidence tools bound to a snapshot.

Tools return structured data and evidence IDs. They are snapshot-bound:
the model cannot supply arbitrary S3 keys or query other runs.
"""

from __future__ import annotations

from datetime import datetime

from coldchain.contracts.enums import EventType, SensorRole, EvidenceKind
from coldchain.contracts.schemas import (
    EvidenceRef,
    Policy,
    SensorMeasurement,
    Snapshot,
)


class ToolContext:
    """Holds snapshot + measurements for all tools in a single run.

    Caches tool results so repeated calls don't recompute.
    """

    def __init__(
        self,
        snapshot: Snapshot,
        measurements: list[SensorMeasurement],
        run_id: str,
    ) -> None:
        self.snapshot = snapshot
        self.measurements = measurements
        self.run_id = run_id
        self._evidence_registry: dict[str, EvidenceRef] = {}
        self._cache: dict[str, dict] = {}

    def register_evidence(self, ref: EvidenceRef) -> None:
        self._evidence_registry[ref.evidence_id] = ref

    def get_evidence(self, evidence_id: str) -> EvidenceRef | None:
        return self._evidence_registry.get(evidence_id)

    def all_evidence_ids(self) -> set[str]:
        return set(self._evidence_registry.keys())


def _make_evidence_id(prefix: str, index: int) -> str:
    return f"ev-{prefix}-{index:04d}"


# ── Tool implementations ───────────────────────────────────────────────────


def get_excursion_summary(ctx: ToolContext) -> dict:
    """Return verified per-sensor metrics and coverage."""
    if "excursion_summary" in ctx._cache:
        return ctx._cache["excursion_summary"]

    result = {"sensors": []}
    for m in ctx.measurements:
        ev_id = _make_evidence_id("excursion", len(ctx._evidence_registry))
        ref = EvidenceRef(
            evidence_id=ev_id,
            snapshot_id=ctx.snapshot.snapshot_id,
            kind=EvidenceKind.derived_metric,
            record_ids=m.evidence_ids,
            summary=(
                f"Sensor {m.sensor_id}: excursion={'yes' if m.excursion_detected else 'no'}, "
                f"out_of_range={m.estimated_out_of_range_seconds}s, "
                f"min={m.observed_min_c}°C, max={m.observed_max_c}°C"
            ),
            method_version="1.0.0",
            input_record_ids=m.evidence_ids,
        )
        ctx.register_evidence(ref)
        result["sensors"].append(
            {
                "sensor_id": m.sensor_id,
                "role": m.role.value,
                "excursion_detected": m.excursion_detected,
                "estimated_out_of_range_seconds": m.estimated_out_of_range_seconds,
                "unknown_duration_seconds": m.unknown_duration_seconds,
                "observed_min_c": m.observed_min_c,
                "observed_max_c": m.observed_max_c,
                "censored_start": m.censored_start,
                "censored_end": m.censored_end,
                "coverage_status": m.coverage_status.value,
                "sample_count": m.sample_count,
                "evidence_id": ev_id,
            }
        )

    ctx._cache["excursion_summary"] = result
    return result


def get_sensor_comparison(ctx: ToolContext) -> dict:
    """Return aligned readings and disagreement evidence between sensors."""
    if "sensor_comparison" in ctx._cache:
        return ctx._cache["sensor_comparison"]

    sensors = {s.sensor_id: s for s in ctx.snapshot.sensors}
    readings_by_sensor: dict[str, list] = {}
    for r in ctx.snapshot.readings:
        readings_by_sensor.setdefault(r.sensor_id, []).append(r)

    # Find reference sensor
    ref_sensor_id = next(
        s.sensor_id for s in ctx.snapshot.sensors if s.role == SensorRole.reference
    )

    comparisons = []
    ref_readings = sorted(
        readings_by_sensor.get(ref_sensor_id, []), key=lambda r: r.observed_at
    )

    for sid, readings in readings_by_sensor.items():
        if sid == ref_sensor_id:
            continue
        sorted_readings = sorted(readings, key=lambda r: r.observed_at)

        # Align readings by timestamp (simple nearest-match)
        aligned = []
        max_diff_c = 0.0
        for cr in sorted_readings:
            nearest_ref = min(
                ref_readings,
                key=lambda rr: abs((rr.observed_at - cr.observed_at).total_seconds()),
                default=None,
            )
            if nearest_ref is None:
                continue
            gap = abs((nearest_ref.observed_at - cr.observed_at).total_seconds())
            if gap > ctx.snapshot.policy.max_gap_seconds:
                continue  # Don't align across large gaps
            diff = round(cr.temperature_c - nearest_ref.temperature_c, 2)
            max_diff_c = max(max_diff_c, abs(diff))
            aligned.append(
                {
                    "time": cr.observed_at.isoformat(),
                    "reference_c": nearest_ref.temperature_c,
                    "comparison_c": cr.temperature_c,
                    "difference_c": diff,
                    "ref_event_id": nearest_ref.event_id,
                    "cmp_event_id": cr.event_id,
                }
            )

        ev_id = _make_evidence_id("comparison", len(ctx._evidence_registry))
        record_ids = [a["ref_event_id"] for a in aligned] + [
            a["cmp_event_id"] for a in aligned
        ]
        ref = EvidenceRef(
            evidence_id=ev_id,
            snapshot_id=ctx.snapshot.snapshot_id,
            kind=EvidenceKind.derived_metric,
            record_ids=record_ids,
            summary=f"Sensor comparison {ref_sensor_id} vs {sid}: max difference {max_diff_c}°C",
            method_version="1.0.0",
            input_record_ids=record_ids,
        )
        ctx.register_evidence(ref)
        comparisons.append(
            {
                "reference_sensor_id": ref_sensor_id,
                "comparison_sensor_id": sid,
                "aligned_readings": aligned,
                "max_difference_c": max_diff_c,
                "evidence_id": ev_id,
            }
        )

    result = {"comparisons": comparisons}
    ctx._cache["sensor_comparison"] = result
    return result


def _get_events_by_type(ctx: ToolContext, event_type: EventType, cache_key: str) -> dict:
    """Generic helper for door/refrigeration/vehicle events."""
    if cache_key in ctx._cache:
        return ctx._cache[cache_key]

    events = [e for e in ctx.snapshot.events if e.event_type == event_type]
    events.sort(key=lambda e: e.observed_at)

    result_events = []
    for e in events:
        ev_id = _make_evidence_id(cache_key, len(ctx._evidence_registry))
        ref = EvidenceRef(
            evidence_id=ev_id,
            snapshot_id=ctx.snapshot.snapshot_id,
            kind=EvidenceKind.event,
            record_ids=[e.event_id],
            observed_at=e.observed_at,
            summary=f"{event_type.value}: {e.value} at {e.observed_at.isoformat()}",
        )
        ctx.register_evidence(ref)
        result_events.append(
            {
                "event_id": e.event_id,
                "observed_at": e.observed_at.isoformat(),
                "value": e.value,
                "source": e.source,
                "evidence_id": ev_id,
            }
        )

    result = {"events": result_events, "count": len(result_events)}
    ctx._cache[cache_key] = result
    return result


def get_door_events(ctx: ToolContext) -> dict:
    """Return door timeline around the excursion window."""
    return _get_events_by_type(ctx, EventType.door_state, "door_events")


def get_refrigeration_events(ctx: ToolContext) -> dict:
    """Return refrigeration running/stopped/fault observations."""
    return _get_events_by_type(ctx, EventType.refrigeration_state, "refrigeration_events")


def get_vehicle_events(ctx: ToolContext) -> dict:
    """Return stationary/moving context."""
    return _get_events_by_type(ctx, EventType.vehicle_state, "vehicle_events")


def get_handling_policy(ctx: ToolContext) -> dict:
    """Return the exact configured policy and version."""
    if "handling_policy" in ctx._cache:
        return ctx._cache["handling_policy"]

    p = ctx.snapshot.policy
    ev_id = _make_evidence_id("policy", len(ctx._evidence_registry))
    ref = EvidenceRef(
        evidence_id=ev_id,
        snapshot_id=ctx.snapshot.snapshot_id,
        kind=EvidenceKind.policy,
        record_ids=[p.policy_id],
        summary=f"Policy {p.policy_id} v{p.policy_version}: {p.min_c}-{p.max_c}°C",
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
