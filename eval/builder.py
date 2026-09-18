"""Construct evaluation snapshots from explicit control points.

Cases are written as a handful of (offset_seconds, temperature) control points and a
list of events, then expanded to a regular sampling cadence by linear interpolation.
Writing the shape by hand is deliberate: docs/VERIFICATION.md requires holdout
patterns constructed independently, not the simulator's templates under new seeds.

Nothing here writes a scenario name, family or expected answer into the snapshot.
The snapshot a case produces carries observations only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from coldchain.contracts.schemas import Event, Policy, Reading, Sensor, Snapshot

# A fixed namespace keeps every generated identifier stable across runs, so the
# dataset digest only changes when a case genuinely changes.
NAMESPACE = uuid.UUID("6f1b6f4e-6a5c-4d7e-9f62-2b7c9f0c7a11")
BASE_TIME = datetime(2026, 9, 18, 8, 0, 0, tzinfo=UTC)
CADENCE_SECONDS = 60

POLICY = Policy(
    policy_id="illustrative-refrigerated-1",
    policy_version="1.0",
    min_c=2.0,
    max_c=8.0,
    expected_interval_seconds=60.0,
    max_gap_seconds=120.0,
)

Point = tuple[int, float]
EventSpec = tuple[int, str, str]


def _uid(case_id: str, suffix: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{case_id}:{suffix}"))


def _at(offset: int) -> datetime:
    return BASE_TIME + timedelta(seconds=offset)


def _interpolate(points: list[Point], cadence: int) -> list[Point]:
    """Expand control points to a regular cadence, keeping the control points exact."""
    if not points:
        return []
    expanded: dict[int, float] = {}
    for (t1, v1), (t2, v2) in zip(points, points[1:], strict=False):
        span = t2 - t1
        offset = t1
        while offset < t2:
            ratio = 0.0 if span == 0 else (offset - t1) / span
            expanded[offset] = round(v1 + (v2 - v1) * ratio, 2)
            offset += cadence
    for t, v in points:
        expanded[t] = round(v, 2)
    return sorted(expanded.items())


@dataclass(frozen=True)
class EvalCase:
    """One frozen holdout case: observations plus the answer it should produce."""

    case_id: str
    family: str
    expected_outcome: str
    expected_hypothesis: str | None
    reference: list[Point]
    comparison: list[Point] | None = None
    events: list[EventSpec] = field(default_factory=list)
    # Offsets whose reference readings are dropped, creating an unobserved gap.
    drop_reference: tuple[int, ...] = ()
    drop_comparison_entirely: bool = False
    note: str = ""

    def snapshot(self) -> Snapshot:
        ref_id = _uid(self.case_id, "sensor.reference")
        cmp_id = _uid(self.case_id, "sensor.comparison")
        sensors = [
            Sensor(sensor_id=ref_id, placement="front_air", role="reference"),
            Sensor(sensor_id=cmp_id, placement="rear_air", role="comparison"),
        ]

        readings: list[Reading] = []
        for offset, temp in _interpolate(self.reference, CADENCE_SECONDS):
            if offset in self.drop_reference:
                continue
            readings.append(
                Reading(
                    event_id=_uid(self.case_id, f"r.ref.{offset}"),
                    sensor_id=ref_id,
                    observed_at=_at(offset),
                    temperature_c=temp,
                )
            )

        if not self.drop_comparison_entirely:
            comparison = self.comparison
            if comparison is None:
                # Mirror the reference a little warmer, as a rear-air sensor reads.
                comparison = [(t, round(v + 0.3, 2)) for t, v in self.reference]
            for offset, temp in _interpolate(comparison, CADENCE_SECONDS):
                readings.append(
                    Reading(
                        event_id=_uid(self.case_id, f"r.cmp.{offset}"),
                        sensor_id=cmp_id,
                        observed_at=_at(offset),
                        temperature_c=temp,
                    )
                )

        events = [
            Event(
                event_id=_uid(self.case_id, f"e.{offset}.{event_type}.{value}"),
                observed_at=_at(offset),
                event_type=event_type,
                value=value,
                source="simulated_controller",
            )
            for offset, event_type, value in self.events
        ]

        last_offset = max(
            [offset for offset, _ in self.reference]
            + [offset for offset, _, _ in self.events]
            + [0]
        )
        return Snapshot(
            snapshot_id=_uid(self.case_id, "snapshot"),
            shipment_id=_uid(self.case_id, "shipment"),
            cutoff_at=_at(last_offset),
            policy=POLICY,
            sensors=sensors,
            readings=readings,
            events=events,
        )
