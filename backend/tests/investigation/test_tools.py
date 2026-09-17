"""Tests for read-only evidence tools."""

import uuid
from datetime import datetime, timedelta, timezone

from coldchain.contracts.enums import EventType, SensorRole
from coldchain.contracts.schemas import (
    Event,
    Policy,
    Reading,
    Sensor,
    SensorMeasurement,
    Snapshot,
)
from coldchain.investigation.tools import (
    ToolContext,
    get_door_events,
    get_excursion_summary,
    get_handling_policy,
    get_refrigeration_events,
    get_sensor_comparison,
)

BASE_TS = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _ts(seconds: int) -> datetime:
    return BASE_TS + timedelta(seconds=seconds)


def _make_ctx() -> ToolContext:
    """Build a ToolContext with a snapshot containing 2 sensors, readings, and events."""
    sensors = [
        Sensor(sensor_id="s-ref", placement="center", role=SensorRole.reference),
        Sensor(sensor_id="s-cmp", placement="door", role=SensorRole.comparison),
    ]
    readings = [
        Reading(event_id="r-0", sensor_id="s-ref", observed_at=_ts(0), temperature_c=5.0),
        Reading(event_id="r-1", sensor_id="s-ref", observed_at=_ts(60), temperature_c=9.0),
        Reading(event_id="r-2", sensor_id="s-ref", observed_at=_ts(120), temperature_c=5.0),
        Reading(event_id="c-0", sensor_id="s-cmp", observed_at=_ts(0), temperature_c=5.0),
        Reading(event_id="c-1", sensor_id="s-cmp", observed_at=_ts(60), temperature_c=5.5),
        Reading(event_id="c-2", sensor_id="s-cmp", observed_at=_ts(120), temperature_c=5.0),
    ]
    events = [
        Event(
            event_id="e-door-1",
            observed_at=_ts(30),
            event_type=EventType.door_state,
            value="open",
            source="sensor",
        ),
        Event(
            event_id="e-door-2",
            observed_at=_ts(90),
            event_type=EventType.door_state,
            value="closed",
            source="sensor",
        ),
        Event(
            event_id="e-refrig-1",
            observed_at=_ts(50),
            event_type=EventType.refrigeration_state,
            value="running",
            source="telemetry",
        ),
        Event(
            event_id="e-vehicle-1",
            observed_at=_ts(10),
            event_type=EventType.vehicle_state,
            value="stopped",
            source="gps",
        ),
    ]
    snapshot = Snapshot(
        snapshot_id="snap-001",
        shipment_id="ship-001",
        cutoff_at=_ts(600),
        policy=Policy(
            policy_id="pol-1",
            policy_version="1.0",
            min_c=2.0,
            max_c=8.0,
            expected_interval_seconds=60.0,
            max_gap_seconds=120.0,
        ),
        sensors=sensors,
        readings=readings,
        events=events,
    )
    measurements = [
        SensorMeasurement(
            sensor_id="s-ref",
            role=SensorRole.reference,
            excursion_detected=True,
            estimated_out_of_range_seconds=15.0,
            sample_count=3,
            observed_min_c=5.0,
            observed_max_c=9.0,
            evidence_ids=["ev-det-0"],
        ),
        SensorMeasurement(
            sensor_id="s-cmp",
            role=SensorRole.comparison,
            excursion_detected=False,
            sample_count=3,
            observed_min_c=5.0,
            observed_max_c=5.5,
            evidence_ids=["ev-det-1"],
        ),
    ]
    return ToolContext(snapshot=snapshot, measurements=measurements, run_id="run-001")


# ── Tests ───────────────────────────────────────────────────────────────────


def test_excursion_summary_returns_evidence_ids():
    ctx = _make_ctx()
    result = get_excursion_summary(ctx)

    assert "sensors" in result
    assert len(result["sensors"]) == 2
    for s in result["sensors"]:
        assert "evidence_id" in s
        assert s["evidence_id"].startswith("ev-excursion-")


def test_sensor_comparison_aligned_readings():
    ctx = _make_ctx()
    result = get_sensor_comparison(ctx)

    assert "comparisons" in result
    assert len(result["comparisons"]) == 1  # one comparison sensor vs reference
    comp = result["comparisons"][0]
    assert comp["reference_sensor_id"] == "s-ref"
    assert comp["comparison_sensor_id"] == "s-cmp"
    assert len(comp["aligned_readings"]) > 0
    for aligned in comp["aligned_readings"]:
        assert "reference_c" in aligned
        assert "comparison_c" in aligned
        assert "difference_c" in aligned


def test_door_events_filtered():
    ctx = _make_ctx()
    result = get_door_events(ctx)

    assert result["count"] == 2
    values = [e["value"] for e in result["events"]]
    assert "open" in values
    assert "closed" in values


def test_refrigeration_events_filtered():
    ctx = _make_ctx()
    result = get_refrigeration_events(ctx)

    assert result["count"] == 1
    assert result["events"][0]["value"] == "running"


def test_handling_policy_returns_policy():
    ctx = _make_ctx()
    result = get_handling_policy(ctx)

    assert result["policy_id"] == "pol-1"
    assert result["min_c"] == 2.0
    assert result["max_c"] == 8.0
    assert result["expected_interval_seconds"] == 60.0
    assert result["max_gap_seconds"] == 120.0
    assert "evidence_id" in result


def test_tool_caching():
    ctx = _make_ctx()
    result1 = get_excursion_summary(ctx)
    result2 = get_excursion_summary(ctx)

    assert result1 is result2


def test_evidence_registry_populated():
    ctx = _make_ctx()
    # Call all tools to populate registry
    get_excursion_summary(ctx)
    get_sensor_comparison(ctx)
    get_door_events(ctx)
    get_refrigeration_events(ctx)
    get_handling_policy(ctx)

    all_ids = ctx.all_evidence_ids()
    assert len(all_ids) > 0
    # Every registered ID should be retrievable
    for eid in all_ids:
        assert ctx.get_evidence(eid) is not None
