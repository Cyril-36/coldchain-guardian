"""Read-only evidence tools bound to a snapshot.

Tools return structured data and frozen EvidenceRef objects. They are snapshot-bound:
the investigator cannot supply arbitrary S3 keys, query other runs, or reference
records outside the authorized snapshot.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from typing import Any

from coldchain.contracts.enums import (
    DoorState,
    EventType,
    EvidenceKind,
    RefrigerationState,
    SensorRole,
)
from coldchain.contracts.schemas import (
    Event,
    EvidenceInterval,
    EvidenceRef,
    Policy,
    Reading,
    SensorMeasurement,
    Snapshot,
)
from coldchain.core.detector import (
    build_measurement_evidence,
    detect_excursions,
    is_excursion_detected,
    is_out_of_range,
    segment_excursion_intervals,
    validate_snapshot,
)

TOOLS_VERSION = "1.0.0"

DISAGREEMENT_THRESHOLD_C = 1.5

# Floating-point slack for comparing interpolated instants against observed ones.
_INTERVAL_TOLERANCE = 1e-6

# Measurement fields that a caller-supplied SensorMeasurement must match exactly.
_VERIFIED_MEASUREMENT_FIELDS = (
    "estimated_out_of_range_seconds",
    "unknown_duration_seconds",
    "observed_min_c",
    "observed_max_c",
    "censored_start",
    "censored_end",
    "coverage_status",
    "sample_count",
    "first_observed_out_at",
    "last_observed_out_at",
)
MAX_ALIGNMENT_OFFSET_SECONDS = 30.0
MAX_RETURNED_ALIGNED_READINGS = 10
MAX_RETURNED_EVENTS = 10

# Durations are reported to one decimal place (CONTRACTS.md), so derived instants are
# rendered on the same 0.1s grid. Truncating an interpolated crossing to a whole second
# instead would make a rendered interval disagree with the duration printed beside it.
_RENDER_DECIMALS = 1


def _iso_z(dt: datetime) -> str:
    """Format an aware UTC datetime as ISO 8601 with a ``Z`` suffix.

    Matches the contract serializer in ``coldchain.contracts.schemas``, and keeps
    millisecond precision for instants that are not whole seconds.
    """
    if dt.microsecond:
        return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return dt.isoformat().replace("+00:00", "Z")


def _instant(ts: float, tz: Any) -> datetime:
    """Convert POSIX seconds to an aware datetime on the reporting grid.

    Interpolated threshold crossings are floating point. Snapping them to the same
    0.1s granularity used for durations means a rendered interval subtracts exactly
    to its own ``duration_seconds``.
    """
    return datetime.fromtimestamp(round(ts, _RENDER_DECIMALS), tz=tz)


def _elapsed_seconds(start: datetime, end: datetime) -> float:
    """Seconds between two instants, at the contract's one-decimal precision."""
    return round((end - start).total_seconds(), _RENDER_DECIMALS)


class ToolContext:
    """Holds snapshot, measurements, and evidence registry for a single run.

    Enforces strict snapshot isolation, cutoff boundaries, and deterministic caching.

    The context owns its data. The snapshot is validated, normalized and deep-copied
    at construction, measurements are recomputed from that copy by the deterministic
    detector, and every public accessor hands out a copy. Once a context exists, no
    mutation — of the caller's original snapshot, of the snapshot or measurements this
    context exposes, or of a dict a tool returned — can change the facts or the
    EvidenceRef items it produces.
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

        # Deep copy so the context owns its data outright: nothing the caller does to
        # the snapshot it passed in can reach the facts computed below.
        clean = validate_snapshot(snapshot).model_copy(deep=True)
        self._snapshot = clean
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
                differing = [
                    f"{field}={getattr(m, field)!r} (expected {getattr(exp, field)!r})"
                    for field in _VERIFIED_MEASUREMENT_FIELDS
                    if getattr(m, field) != getattr(exp, field)
                ]
                if differing:
                    raise ValueError(
                        f"Supplied measurement for sensor {m.sensor_id} does not match "
                        f"deterministic detector result: " + ", ".join(differing) + "."
                    )

        # Measurements always come from the deterministic detector, never from the
        # caller. Supplied values are only ever used to fail fast on a mismatch.
        self._measurements: tuple[SensorMeasurement, ...] = tuple(expected_measurements)

        self._valid_record_ids: set[str] = {
            r.event_id for r in clean.readings
        } | {e.event_id for e in clean.events}

        self._evidence_registry: dict[str, EvidenceRef] = {}
        self._cache: dict[str, dict[str, Any]] = {}

    # ── Public, read-only views ────────────────────────────────────────────
    #
    # These hand out defensive copies. A caller may inspect or even mutate what it
    # receives; the canonical data behind ``_snapshot`` and ``_measurements``, and
    # therefore every fact and EvidenceRef this context produces, is unaffected.
    # Tools in this module read the canonical private attributes directly.

    @property
    def snapshot(self) -> Snapshot:
        """A copy of the authorized snapshot. Mutating it does not affect evidence."""
        return self._snapshot.model_copy(deep=True)

    @property
    def measurements(self) -> list[SensorMeasurement]:
        """Copies of the detector measurements. Mutating them does not affect evidence."""
        return [m.model_copy(deep=True) for m in self._measurements]

    @property
    def snapshot_id(self) -> str:
        return self._snapshot.snapshot_id

    @property
    def cutoff_at(self) -> datetime:
        return self._snapshot.cutoff_at

    def cached(self, key: str, build: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        """Return a copy of the cached result for *key*, building it on first use.

        Copying on the way out means a caller that mutates a tool result cannot
        corrupt what a later call to the same tool returns.
        """
        if key not in self._cache:
            self._cache[key] = build()
        return deepcopy(self._cache[key])

    def register_evidence(self, ref: EvidenceRef) -> None:
        """Register an evidence item, enforcing provenance, record ID, and cutoff checks."""
        if ref.snapshot_id != self._snapshot.snapshot_id:
            raise ValueError(
                f"Evidence {ref.evidence_id} snapshot_id ({ref.snapshot_id}) does not "
                f"match context snapshot_id ({self._snapshot.snapshot_id})"
            )

        # Validate that all record_ids belong to the snapshot
        for rec_id in ref.record_ids:
            if rec_id not in self._valid_record_ids:
                raise ValueError(
                    f"Evidence {ref.evidence_id} references unknown record_id: {rec_id}"
                )

        # Cutoff checks
        if ref.observed_at is not None and ref.observed_at > self._snapshot.cutoff_at:
            raise ValueError(
                f"Evidence {ref.evidence_id} observed_at {ref.observed_at} "
                f"exceeds cutoff {self._snapshot.cutoff_at}"
            )
        if ref.interval is not None and ref.interval.end_at > self._snapshot.cutoff_at:
            raise ValueError(
                f"Evidence {ref.evidence_id} interval end {ref.interval.end_at} "
                f"exceeds cutoff {self._snapshot.cutoff_at}"
            )

        self._evidence_registry[ref.evidence_id] = ref.model_copy(deep=True)

    def get_evidence(self, evidence_id: str) -> EvidenceRef | None:
        """Retrieve a copy of a registered evidence reference by ID."""
        ref = self._evidence_registry.get(evidence_id)
        return ref.model_copy(deep=True) if ref is not None else None

    def all_evidence(self) -> list[EvidenceRef]:
        """Return copies of all registered evidence references, sorted by evidence_id."""
        return [
            self._evidence_registry[eid].model_copy(deep=True)
            for eid in sorted(self._evidence_registry.keys())
        ]

    def all_evidence_ids(self) -> set[str]:
        """Return set of all registered evidence IDs."""
        return set(self._evidence_registry.keys())


# ── Tool Implementations ───────────────────────────────────────────────────


def get_excursion_summary(ctx: ToolContext) -> dict[str, Any]:
    """Return verified per-sensor metrics, coverage, and deterministic evidence."""
    def build() -> dict[str, Any]:
        return _build_excursion_summary(ctx)

    return ctx.cached("excursion_summary", build)


def _build_excursion_summary(ctx: ToolContext) -> dict[str, Any]:
    # Register measurement evidence from detector
    detector_refs = build_measurement_evidence(
        ctx._snapshot, ctx._measurements, policy=ctx._snapshot.policy
    )
    for ref in detector_refs:
        ctx.register_evidence(ref)

    sensor_summaries: list[dict[str, Any]] = []
    sensors_by_id = {s.sensor_id: s for s in ctx._snapshot.sensors}

    for m in ctx._measurements:
        sensor = sensors_by_id.get(m.sensor_id)
        if sensor is None:
            raise ValueError(f"Measurement references unknown sensor: {m.sensor_id}")
        role_str = sensor.role.value
        placement_str = sensor.placement
        ev_id = m.evidence_ids[0] if m.evidence_ids else str(
            uuid.uuid5(uuid.UUID(ctx._snapshot.snapshot_id), f"measurement:{m.sensor_id}")
        )

        sensor_summaries.append(
            {
                "sensor_id": m.sensor_id,
                "role": role_str,
                "placement": placement_str,
                "excursion_detected": is_excursion_detected(m),
                "estimated_out_of_range_seconds": m.estimated_out_of_range_seconds,
                "unknown_duration_seconds": m.unknown_duration_seconds,
                "observed_min_c": m.observed_min_c,
                "observed_max_c": m.observed_max_c,
                "censored_start": m.censored_start,
                "censored_end": m.censored_end,
                "coverage_status": m.coverage_status.value,
                "sample_count": m.sample_count,
                "first_observed_out_at": (
                    _iso_z(m.first_observed_out_at)
                    if m.first_observed_out_at
                    else None
                ),
                "last_observed_out_at": (
                    _iso_z(m.last_observed_out_at)
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
    return result


def get_sensor_comparison(ctx: ToolContext) -> dict[str, Any]:
    """Return aligned readings and disagreement evidence between sensors.

    Does NOT interpolate or align readings across gaps exceeding max alignment tolerance.
    Where observations cannot support comparison, returns insufficient/unaligned evidence
    rather than a confirmed disagreement. Evidence intervals describe only readings actually used.
    """
    def build() -> dict[str, Any]:
        return _build_sensor_comparison(ctx)

    return ctx.cached("sensor_comparison", build)


def _build_sensor_comparison(ctx: ToolContext) -> dict[str, Any]:
    ref_sensor = next(
        (s for s in ctx._snapshot.sensors if s.role == SensorRole.reference),
        None,
    )
    if ref_sensor is None:
        raise ValueError("Snapshot does not contain a configured reference sensor")

    cmp_sensors = [s for s in ctx._snapshot.sensors if s.role == SensorRole.comparison]

    readings_by_sensor: dict[str, list[Reading]] = {}
    for r in ctx._snapshot.readings:
        readings_by_sensor.setdefault(r.sensor_id, []).append(r)

    ref_readings = sorted(
        readings_by_sensor.get(ref_sensor.sensor_id, []),
        key=lambda r: (r.observed_at, r.event_id),
    )

    comparisons: list[dict[str, Any]] = []
    max_alignment_seconds = min(
        MAX_ALIGNMENT_OFFSET_SECONDS,
        ctx._snapshot.policy.expected_interval_seconds / 2.0,
    )

    for cmp_sensor in cmp_sensors:
        cmp_readings = sorted(
            readings_by_sensor.get(cmp_sensor.sensor_id, []),
            key=lambda r: (r.observed_at, r.event_id),
        )

        ev_id = str(
            uuid.uuid5(
                uuid.UUID(ctx._snapshot.snapshot_id),
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
                snapshot_id=ctx._snapshot.snapshot_id,
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
                    "time": _iso_z(cr.observed_at),
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
                snapshot_id=ctx._snapshot.snapshot_id,
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
            snapshot_id=ctx._snapshot.snapshot_id,
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

    return {"comparisons": comparisons, "count": len(comparisons)}


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
    def build() -> dict[str, Any]:
        return _build_events_by_type(ctx, event_type, event_label)

    return ctx.cached(cache_key, build)


def _build_events_by_type(
    ctx: ToolContext,
    event_type: EventType,
    event_label: str,
) -> dict[str, Any]:
    events = [e for e in ctx._snapshot.events if e.event_type == event_type]
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
        return result

    result_events: list[dict[str, Any]] = []
    for e in events:
        ev_id = str(
            uuid.uuid5(uuid.UUID(ctx._snapshot.snapshot_id), f"event:{e.event_id}")
        )
        time_str = _iso_z(e.observed_at)
        ref = EvidenceRef(
            evidence_id=ev_id,
            snapshot_id=ctx._snapshot.snapshot_id,
            kind=EvidenceKind.event,
            record_ids=[e.event_id],
            observed_at=e.observed_at,
            summary=f"{event_type.value}: {e.value} at {time_str}",
        )
        ctx.register_evidence(ref)
        result_events.append(
            {
                "event_id": e.event_id,
                "observed_at": _iso_z(e.observed_at),
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
    return result


def _get_valid_excursion_intervals(
    readings: list[Reading],
    policy: Policy,
) -> list[tuple[float, float]]:
    """Compute excursion intervals strictly from observed or validly interpolated intervals.

    Unlike the detector's window-counting helper, this function NEVER bridges unobserved
    gaps (gaps > policy.max_gap_seconds). Intervals across unobserved gaps are excluded
    because no excursion duration can be established within them.
    """
    if len(readings) < 2:
        return []

    raw_intervals: list[tuple[float, float]] = []

    for i in range(len(readings) - 1):
        r1, r2 = readings[i], readings[i + 1]
        t1, t2 = r1.observed_at.timestamp(), r2.observed_at.timestamp()
        gap = t2 - t1

        if gap > policy.max_gap_seconds:
            # Unobserved data gap: do not interpolate across unobserved intervals
            continue

        seg_intervals = segment_excursion_intervals(
            t1, r1.temperature_c, t2, r2.temperature_c, policy.min_c, policy.max_c
        )
        raw_intervals.extend(seg_intervals)

    if not raw_intervals:
        return []

    sorted_intervals = sorted(raw_intervals, key=lambda iv: (iv[0], iv[1]))
    merged: list[tuple[float, float]] = [sorted_intervals[0]]

    for current in sorted_intervals[1:]:
        prev_start, prev_end = merged[-1]
        curr_start, curr_end = current

        if curr_start <= prev_end + 1e-6:
            merged[-1] = (prev_start, max(prev_end, curr_end))
        else:
            merged.append(current)

    return merged


def _covered_spans(
    readings: list[Reading],
    policy: Policy,
) -> list[tuple[float, float]]:
    """Return maximal time spans over which a sensor's coverage is continuous.

    Two consecutive readings separated by at most ``policy.max_gap_seconds`` are
    treated as continuously covered; a wider separation ends the span and starts a
    new one. Time outside these spans is unobserved: nothing about the temperature
    path there can be established, in either direction.
    """
    if len(readings) < 2:
        return []

    spans: list[tuple[float, float]] = []
    span_start = readings[0].observed_at.timestamp()
    previous = span_start

    for r in readings[1:]:
        current = r.observed_at.timestamp()
        if current - previous > policy.max_gap_seconds:
            if previous > span_start:
                spans.append((span_start, previous))
            span_start = current
        previous = current

    if previous > span_start:
        spans.append((span_start, previous))

    return spans


def _covered_seconds_within(
    spans: list[tuple[float, float]],
    window_start: float,
    window_end: float,
) -> float:
    """Return the seconds of *window* that fall inside continuously covered spans."""
    if window_end <= window_start:
        return 0.0
    return sum(
        max(0.0, min(span_end, window_end) - max(span_start, window_start))
        for span_start, span_end in spans
    )


def _classify_excursion_interval_end(
    interval_end: float,
    readings: list[Reading],
    spans: list[tuple[float, float]],
    policy: Policy,
) -> tuple[bool, str | None]:
    """Decide whether an excursion interval ended by returning to the normal range.

    A valid excursion interval can end for two very different reasons: the
    temperature path came back into range, or observation simply stopped (a data gap,
    or the final reading). Only the first is a recovery.

    Returns ``(recovered, reason)``; ``reason`` is set only when not recovered.
    """
    # An observed reading sitting exactly at the interval end settles it directly.
    # The policy range is inclusive, so a reading on the threshold is a return to
    # range, and the last reading of a snapshot is as good an observation as any.
    for r in readings:
        if abs(r.observed_at.timestamp() - interval_end) <= _INTERVAL_TOLERANCE:
            if not is_out_of_range(r.temperature_c, policy.min_c, policy.max_c):
                return True, None
            break

    for span_start, span_end in spans:
        if span_start <= interval_end <= span_end:
            if interval_end < span_end - _INTERVAL_TOLERANCE:
                # An interpolated crossing strictly inside a covered span: the path
                # was still being observed immediately afterwards.
                return True, None
            if readings and (
                interval_end >= readings[-1].observed_at.timestamp() - _INTERVAL_TOLERANCE
            ):
                return False, "observed_coverage_ends_while_still_out_of_range"
            return False, "observed_coverage_ends_at_a_data_gap_before_any_return_to_range"

    return False, "observed_coverage_ends_at_a_data_gap_before_any_return_to_range"


def _confirmed_open_window(
    open_event: Event,
    sorted_door_events: list[Event],
    cutoff_at: datetime,
) -> tuple[datetime, Event | None, Event | None, datetime | None]:
    """Resolve how long the door is *confirmed* open after ``open_event``.

    Returns ``(confirmed_open_until, terminating_event, close_event,
    door_state_unknown_until)``.

    The door is confirmed open only until the first later event reporting a different
    state. An explicit ``unknown`` ends that window exactly as ``closed`` does: after
    it the door state is not established, and unknown state must never be counted as
    confirmed open. When the window ends in ``unknown`` there is no defensible close
    instant either, even if a later ``closed`` event exists, because the door could
    have closed anywhere in the unknown span — so ``close_event`` stays None.
    """
    later = [e for e in sorted_door_events if e.observed_at > open_event.observed_at]
    terminator = next((e for e in later if e.value != DoorState.open), None)

    if terminator is None:
        return cutoff_at, None, None, None

    if terminator.value == DoorState.closed:
        return terminator.observed_at, terminator, terminator, None

    # terminator.value is DoorState.unknown: the state is unknown from here until the
    # next event that reports a definite state, or the cutoff.
    next_definite = next(
        (
            e
            for e in later
            if e.observed_at > terminator.observed_at and e.value != DoorState.unknown
        ),
        None,
    )
    unknown_until = next_definite.observed_at if next_definite else cutoff_at
    return terminator.observed_at, terminator, None, unknown_until


def _compute_door_temporal_facts(
    ctx: ToolContext,
    door_events: list[Event],
) -> dict[str, Any] | None:
    """Compute deterministic temporal facts relating door state to the excursion.

    Reports door opening before rise, overlap with measured excursion coverage, and
    recovery after close. Every claim is confined to observed or validly interpolated
    coverage:

    - Overlap is reported as separate covered segments. Disjoint segments are never
      collapsed into one interval, and unobserved time inside the door-open window is
      reported separately as ``overlap_unknown_seconds``.
    - The door is confirmed open only until the first later event reporting a
      different state. An explicit ``unknown`` ends the confirmed window exactly as
      ``closed`` does, and the unknown span is reported as
      ``door_state_unknown_seconds`` rather than counted as open.
    - Recovery is claimed only when the excursion interval containing the door close
      ends with a transition back into range inside continuous coverage, or at an
      observed reading that is inside the inclusive policy range. Otherwise the result
      is "not established" — never "did not recover".

    Absence of evidence is never reported as evidence of absence, and a temporal
    association never claims causation.
    """
    if not door_events:
        return None

    ref_sensor = next(
        (s for s in ctx._snapshot.sensors if s.role == SensorRole.reference),
        None,
    )
    if ref_sensor is None:
        return None

    ref_measurement = next(
        (m for m in ctx._measurements if m.sensor_id == ref_sensor.sensor_id),
        None,
    )
    if ref_measurement is None:
        return None

    if not is_excursion_detected(ref_measurement):
        return {
            "has_excursion": False,
            "door_opened_before_rise": False,
            "lead_time_seconds": None,
            "overlap_interval": None,
            "overlap_segments": [],
            "overlap_measured_seconds": 0.0,
            "overlap_unknown_seconds": 0.0,
            "overlap_status": "not_applicable",
            "door_state_unknown_seconds": 0.0,
            "temperature_recovered_after_close": False,
            "recovery_time_seconds": None,
            "recovery_status": "not_applicable",
            "recovery_reason": None,
            "record_ids": [],
            "method_version": TOOLS_VERSION,
            "summary": (
                "No excursion detected on reference sensor; "
                "temporal door correlation not applicable."
            ),
            "evidence_id": None,
        }

    # Reference readings
    ref_readings = [r for r in ctx._snapshot.readings if r.sensor_id == ref_sensor.sensor_id]
    ref_readings.sort(key=lambda r: (r.observed_at, r.event_id))

    policy = ctx._snapshot.policy
    valid_intervals = _get_valid_excursion_intervals(ref_readings, policy)
    covered_spans = _covered_spans(ref_readings, policy)

    tz = ref_readings[0].observed_at.tzinfo if ref_readings else None

    # Excursion rise timestamp (from valid interpolated interval if present,
    # else first observed out)
    t_rise: datetime | None = None
    if valid_intervals:
        w_start_ts = valid_intervals[0][0]
        t_rise = _instant(w_start_ts, tz)
        if (
            ref_measurement.first_observed_out_at is not None
            and ref_measurement.first_observed_out_at < t_rise
        ):
            t_rise = ref_measurement.first_observed_out_at
    else:
        t_rise = ref_measurement.first_observed_out_at

    used_record_ids: set[str] = set()
    used_timestamps: list[datetime] = []
    if t_rise is not None:
        used_timestamps.append(t_rise)

    if valid_intervals:
        for iv_start_ts, iv_end_ts in valid_intervals:
            used_timestamps.append(_instant(iv_end_ts, tz))
            for i, r in enumerate(ref_readings):
                r_ts = r.observed_at.timestamp()
                if iv_start_ts <= r_ts <= iv_end_ts:
                    used_record_ids.add(r.event_id)
                    used_timestamps.append(r.observed_at)
                if i + 1 < len(ref_readings):
                    next_ts = ref_readings[i + 1].observed_at.timestamp()
                    if r_ts < iv_start_ts < next_ts:
                        used_record_ids.add(r.event_id)
                        used_record_ids.add(ref_readings[i + 1].event_id)
                        used_timestamps.append(r.observed_at)
                        used_timestamps.append(ref_readings[i + 1].observed_at)
                    if r_ts < iv_end_ts < next_ts:
                        used_record_ids.add(r.event_id)
                        used_record_ids.add(ref_readings[i + 1].event_id)
                        used_timestamps.append(r.observed_at)
                        used_timestamps.append(ref_readings[i + 1].observed_at)
    else:
        # No valid interpolated intervals (e.g. out-of-range readings across unobserved gaps)
        for r in ref_readings:
            if is_out_of_range(r.temperature_c, policy.min_c, policy.max_c):
                used_record_ids.add(r.event_id)
                used_timestamps.append(r.observed_at)

    sorted_door_events = sorted(door_events, key=lambda e: (e.observed_at, e.event_id))
    open_events = [e for e in sorted_door_events if e.value == DoorState.open]

    # 1. Door opening before rise
    prior_open = (
        [e for e in open_events if e.observed_at <= t_rise]
        if t_rise is not None
        else []
    )

    if prior_open:
        open_event = max(prior_open, key=lambda e: e.observed_at)
        door_opened_before_rise = True
        lead_time_seconds = _elapsed_seconds(open_event.observed_at, t_rise)
        used_record_ids.add(open_event.event_id)
        used_timestamps.append(open_event.observed_at)
    else:
        door_opened_before_rise = False
        lead_time_seconds = None
        open_event = open_events[0] if open_events else None
        if open_event:
            used_record_ids.add(open_event.event_id)
            used_timestamps.append(open_event.observed_at)

    # 2. Overlap between the door-open window and measured excursion coverage.
    #
    # Overlap is only ever measured inside validly interpolated intervals. An
    # unobserved gap inside the door-open window is reported as unknown time, never
    # folded into a covered segment and never treated as proof that no overlap
    # occurred there.
    overlap_interval: dict[str, Any] | None = None
    overlap_segments_out: list[dict[str, Any]] = []
    overlap_measured_seconds = 0.0
    overlap_unknown_seconds = 0.0
    overlap_status = "no_door_open_event_observed"
    door_state_unknown_seconds = 0.0
    close_event: Event | None = None
    terminating_event: Event | None = None
    if open_event:
        (
            confirmed_open_until,
            terminating_event,
            close_event,
            door_state_unknown_until,
        ) = _confirmed_open_window(open_event, sorted_door_events, ctx._snapshot.cutoff_at)

        if terminating_event is not None:
            used_record_ids.add(terminating_event.event_id)
            used_timestamps.append(terminating_event.observed_at)

        if door_state_unknown_until is not None and terminating_event is not None:
            door_state_unknown_seconds = _elapsed_seconds(
                terminating_event.observed_at, door_state_unknown_until
            )

        door_open_ts = open_event.observed_at.timestamp()
        # Overlap is measured against the *confirmed* open window only. Time during
        # which the door state is unknown is reported separately and never counted
        # as confirmed open.
        door_close_ts = confirmed_open_until.timestamp()

        # Calculate overlap strictly from observed or validly interpolated intervals
        overlap_segments: list[tuple[float, float]] = []
        for v_start, v_end in valid_intervals:
            ov_start = max(door_open_ts, v_start)
            ov_end = min(door_close_ts, v_end)
            if ov_start < ov_end:
                overlap_segments.append((ov_start, ov_end))

        # Unobserved time inside the door-open window. This is the portion of the
        # window for which no overlap claim can be made either way.
        door_window_seconds = max(0.0, door_close_ts - door_open_ts)
        overlap_unknown_seconds = round(
            door_window_seconds
            - _covered_seconds_within(covered_spans, door_open_ts, door_close_ts),
            1,
        )

        for seg_start, seg_end in overlap_segments:
            start_dt = _instant(seg_start, tz)
            end_dt = _instant(seg_end, tz)
            overlap_segments_out.append(
                {
                    "start_at": _iso_z(start_dt),
                    "end_at": _iso_z(end_dt),
                    "duration_seconds": _elapsed_seconds(start_dt, end_dt),
                }
            )
            used_timestamps.append(start_dt)
            used_timestamps.append(end_dt)

        overlap_measured_seconds = round(
            sum(seg["duration_seconds"] for seg in overlap_segments_out), 1
        )

        if len(overlap_segments) == 1:
            # A single covered segment is genuinely continuous, so it can also be
            # presented as one interval.
            overlap_interval = dict(overlap_segments_out[0])
            overlap_status = "measured_contiguous"
        elif len(overlap_segments) > 1:
            # Disjoint segments separated by unobserved time. Deliberately leave
            # overlap_interval unset: collapsing these into one span would describe
            # the gap as part of a continuous overlap.
            overlap_interval = None
            overlap_status = "measured_disjoint"
        elif overlap_unknown_seconds > 0.0:
            overlap_status = "not_established"
        else:
            overlap_status = "none_within_covered_data"

    # 3. Recovery after close.
    #
    # Recovery is claimed only when the excursion interval containing the door close
    # ends with an observed or validly interpolated transition back into range,
    # inside continuous coverage. An interval that ends at a data gap or at the last
    # reading ended because observation stopped, not because the temperature
    # recovered, so the outcome there is "not established" rather than "did not
    # recover".
    temperature_recovered_after_close = False
    recovery_time_seconds: float | None = None
    recovery_status = "not_established"
    recovery_reason: str | None = None

    if close_event is None:
        if terminating_event is not None and terminating_event.value == DoorState.unknown:
            # A later `closed` event may exist, but the door could have closed
            # anywhere inside the unknown span, so no close instant is defensible.
            recovery_reason = "door_state_unknown_before_any_observed_close"
        else:
            recovery_reason = "no_door_close_event_observed_before_cutoff"
    else:
        close_ts = close_event.observed_at.timestamp()
        containing = next(
            ((s, e) for s, e in valid_intervals if s <= close_ts < e),
            None,
        )
        if containing is None:
            recovery_reason = "door_close_not_within_measured_excursion_coverage"
        else:
            recovered, reason = _classify_excursion_interval_end(
                containing[1], ref_readings, covered_spans, policy
            )
            if recovered:
                temperature_recovered_after_close = True
                recovery_status = "recovered"
                recovery_dt = _instant(containing[1], tz)
                recovery_time_seconds = _elapsed_seconds(close_event.observed_at, recovery_dt)
                used_timestamps.append(recovery_dt)
            else:
                recovery_reason = reason

    # Narrative describing temporal association without claiming causation
    parts: list[str] = []
    if door_opened_before_rise:
        parts.append(
            f"Door open event observed {lead_time_seconds}s prior to "
            "temperature rise above threshold."
        )
    else:
        parts.append("Door open event was not observed prior to temperature rise.")

    if overlap_status == "measured_contiguous" and overlap_interval is not None:
        sentence = (
            f"Door-open and excursion overlap measured "
            f"{overlap_interval['duration_seconds']}s "
            f"({overlap_interval['start_at']} to {overlap_interval['end_at']})"
        )
        if overlap_unknown_seconds > 0.0:
            sentence += (
                f"; a further {overlap_unknown_seconds}s of the door-open window is "
                "unobserved, so the total exposure may be longer"
            )
        parts.append(sentence + ".")
    elif overlap_status == "measured_disjoint":
        rendered = ", ".join(
            f"{seg['start_at']} to {seg['end_at']} ({seg['duration_seconds']}s)"
            for seg in overlap_segments_out
        )
        parts.append(
            f"Door-open and excursion overlap measured {overlap_measured_seconds}s across "
            f"{len(overlap_segments_out)} separately observed segments ({rendered}); "
            f"{overlap_unknown_seconds}s of the door-open window is unobserved, so these "
            "segments cannot be shown to be one continuous overlap."
        )
    elif overlap_status == "not_established":
        parts.append(
            "No overlap between the open door and measured excursion coverage could be "
            f"established: {overlap_unknown_seconds}s of the door-open window is unobserved, "
            "so an overlap there can be neither measured nor ruled out."
        )
    elif overlap_status == "none_within_covered_data":
        parts.append(
            "No overlap between the confirmed open door and the excursion window "
            "within continuously observed coverage."
        )
    else:
        parts.append("No door open event was observed in the snapshot.")

    if door_state_unknown_seconds > 0.0:
        parts.append(
            f"Door state is reported unknown for {door_state_unknown_seconds}s after the "
            "confirmed open window; that time is not counted as open and the door may "
            "have been open or closed during it."
        )

    if temperature_recovered_after_close:
        parts.append(
            f"Temperature returned to normal range {recovery_time_seconds}s after door closed."
        )
    elif recovery_reason == "door_state_unknown_before_any_observed_close":
        parts.append(
            "Recovery after door close could not be established: door state becomes "
            "unknown before any observed close, so no close instant is defensible."
        )
    elif recovery_reason == "no_door_close_event_observed_before_cutoff":
        parts.append(
            "No door close event was observed prior to cutoff, so recovery after close "
            "could not be established."
        )
    elif recovery_reason == "observed_coverage_ends_while_still_out_of_range":
        parts.append(
            "Recovery after door close could not be established: observed readings end "
            "while still out of range, and the snapshot does not extend further."
        )
    elif recovery_reason == "door_close_not_within_measured_excursion_coverage":
        parts.append(
            "Recovery after door close could not be established: the door close does not "
            "fall inside a measured excursion interval."
        )
    else:
        parts.append(
            "Recovery after door close could not be established: observed coverage ends at "
            "a data gap before any return to normal range."
        )

    parts.append("Temporal association observed; does not establish causation.")
    summary = " ".join(parts)

    ev_id = str(uuid.uuid5(uuid.UUID(ctx._snapshot.snapshot_id), "door_temporal_facts"))
    sorted_times = sorted(used_timestamps)
    interval = (
        EvidenceInterval(start_at=sorted_times[0], end_at=sorted_times[-1])
        if sorted_times
        else None
    )

    ref = EvidenceRef(
        evidence_id=ev_id,
        snapshot_id=ctx._snapshot.snapshot_id,
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
        # Present only when the overlap is a single continuously covered segment.
        # None whenever the covered segments are disjoint, so that an unobserved gap
        # is never rendered as part of one continuous interval.
        "overlap_interval": overlap_interval,
        "overlap_segments": overlap_segments_out,
        "overlap_measured_seconds": overlap_measured_seconds,
        "overlap_unknown_seconds": overlap_unknown_seconds,
        "overlap_status": overlap_status,
        # Time during which the door state itself is not established. Distinct from
        # overlap_unknown_seconds, which is unobserved *temperature* coverage.
        "door_state_unknown_seconds": door_state_unknown_seconds,
        "temperature_recovered_after_close": temperature_recovered_after_close,
        "recovery_time_seconds": recovery_time_seconds,
        "recovery_status": recovery_status,
        "recovery_reason": recovery_reason,
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
    def build() -> dict[str, Any]:
        return _build_door_events(ctx)

    return ctx.cached("door_events", build)


def _build_door_events(ctx: ToolContext) -> dict[str, Any]:
    # _get_events_by_type already returns a copy, so mutating `raw` here cannot
    # disturb the cached door_events_raw entry.
    raw = _get_events_by_type(ctx, EventType.door_state, "door_events_raw", "door")
    if raw["is_missing"]:
        raw["temporal_facts"] = None
        return raw

    door_events_list = [e for e in ctx._snapshot.events if e.event_type == EventType.door_state]
    temporal_facts = _compute_door_temporal_facts(ctx, door_events_list)
    raw["temporal_facts"] = temporal_facts
    if (
        temporal_facts
        and temporal_facts.get("evidence_id")
        and temporal_facts["evidence_id"] not in raw["evidence_ids"]
    ):
        raw["evidence_ids"].append(temporal_facts["evidence_id"])

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
        all_refrig = [
            e for e in ctx._snapshot.events if e.event_type == EventType.refrigeration_state
        ]
        res["has_fault_or_stopped"] = any(
            e.value in (RefrigerationState.fault, RefrigerationState.stopped)
            for e in all_refrig
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
    def build() -> dict[str, Any]:
        return _build_handling_policy(ctx)

    return ctx.cached("handling_policy", build)


def _build_handling_policy(ctx: ToolContext) -> dict[str, Any]:
    p = ctx._snapshot.policy
    ev_id = str(uuid.uuid5(uuid.UUID(ctx._snapshot.snapshot_id), "policy"))
    ref = EvidenceRef(
        evidence_id=ev_id,
        snapshot_id=ctx._snapshot.snapshot_id,
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
    return result
