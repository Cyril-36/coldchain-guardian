"""Tests for read-only, snapshot-bound evidence tools."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from coldchain.contracts.enums import (
    Assessment,
    EventType,
    EvidenceKind,
    GenerationMode,
    HypothesisType,
    NextCheckCode,
    Outcome,
    SensorRole,
    VerificationStatus,
)
from coldchain.contracts.schemas import (
    Event,
    EvidenceRef,
    Hypothesis,
    NextCheck,
    Policy,
    Reading,
    Report,
    Sensor,
    Snapshot,
    Verification,
)
from coldchain.core.detector import detect_excursions
from coldchain.investigation.tools import (
    TOOLS_VERSION,
    ToolContext,
    get_door_events,
    get_excursion_summary,
    get_handling_policy,
    get_refrigeration_events,
    get_sensor_comparison,
    get_vehicle_events,
)

BASE_TS = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _ts(seconds: int) -> datetime:
    return BASE_TS + timedelta(seconds=seconds)


def _make_snapshot(
    readings: list[Reading] | None = None,
    events: list[Event] | None = None,
    sensors: list[Sensor] | None = None,
    policy: Policy | None = None,
    cutoff_seconds: int = 600,
) -> Snapshot:
    """Helper to create a valid Snapshot for testing."""
    if policy is None:
        policy = Policy(
            policy_id="policy-001",
            policy_version="1.0",
            min_c=2.0,
            max_c=8.0,
            expected_interval_seconds=60.0,
            max_gap_seconds=120.0,
        )

    ref_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.ref"))
    cmp_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.cmp"))

    if sensors is None:
        sensors = [
            Sensor(sensor_id=ref_id, placement="center", role=SensorRole.reference),
            Sensor(sensor_id=cmp_id, placement="door", role=SensorRole.comparison),
        ]

    if readings is None:
        readings = [
            Reading(
                event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "r.0")),
                sensor_id=ref_id,
                observed_at=_ts(0),
                temperature_c=5.0,
            ),
            Reading(
                event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "r.1")),
                sensor_id=ref_id,
                observed_at=_ts(60),
                temperature_c=9.0,
            ),
            Reading(
                event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "r.2")),
                sensor_id=ref_id,
                observed_at=_ts(120),
                temperature_c=5.0,
            ),
            Reading(
                event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "c.0")),
                sensor_id=cmp_id,
                observed_at=_ts(0),
                temperature_c=5.0,
            ),
            Reading(
                event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "c.1")),
                sensor_id=cmp_id,
                observed_at=_ts(60),
                temperature_c=5.5,
            ),
            Reading(
                event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "c.2")),
                sensor_id=cmp_id,
                observed_at=_ts(120),
                temperature_c=5.0,
            ),
        ]

    if events is None:
        events = [
            Event(
                event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "e.door.1")),
                observed_at=_ts(30),
                event_type=EventType.door_state,
                value="open",
                source="sensor",
            ),
            Event(
                event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "e.door.2")),
                observed_at=_ts(90),
                event_type=EventType.door_state,
                value="closed",
                source="sensor",
            ),
            Event(
                event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "e.refrig.1")),
                observed_at=_ts(50),
                event_type=EventType.refrigeration_state,
                value="running",
                source="telemetry",
            ),
            Event(
                event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "e.veh.1")),
                observed_at=_ts(10),
                event_type=EventType.vehicle_state,
                value="stopped",
                source="gps",
            ),
        ]

    return Snapshot(
        snapshot_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "snapshot.test")),
        shipment_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "shipment.test")),
        cutoff_at=_ts(cutoff_seconds),
        policy=policy,
        sensors=sensors,
        readings=readings,
        events=events,
    )


# ── ToolContext Tests ───────────────────────────────────────────────────────


def test_tool_context_invalid_snapshot_id_rejected():
    snap = _make_snapshot()
    # Malform snapshot_id
    object.__setattr__(snap, "snapshot_id", "not-a-uuid")
    with pytest.raises(ValueError, match="Invalid snapshot_id UUID"):
        ToolContext(snap)


def test_tool_context_cutoff_violation_rejected():
    snap = _make_snapshot()
    # Add a reading after cutoff
    late_reading = Reading(
        event_id=str(uuid.uuid4()),
        sensor_id=snap.sensors[0].sensor_id,
        observed_at=snap.cutoff_at + timedelta(seconds=10),
        temperature_c=5.0,
    )
    object.__setattr__(snap, "readings", snap.readings + [late_reading])
    with pytest.raises(ValueError, match="occurs after cutoff"):
        ToolContext(snap)


def test_tool_context_register_evidence_mismatched_snapshot_rejected():
    snap = _make_snapshot()
    ctx = ToolContext(snap)
    foreign_sid = str(uuid.uuid4())
    ref = EvidenceRef(
        evidence_id=str(uuid.uuid4()),
        snapshot_id=foreign_sid,
        kind=EvidenceKind.derived_metric,
        record_ids=[],
        summary="Foreign evidence",
    )
    with pytest.raises(ValueError, match="does not match context snapshot_id"):
        ctx.register_evidence(ref)


def test_tool_context_register_evidence_unknown_record_id_rejected():
    snap = _make_snapshot()
    ctx = ToolContext(snap)
    unknown_rec = str(uuid.uuid4())
    ref = EvidenceRef(
        evidence_id=str(uuid.uuid4()),
        snapshot_id=snap.snapshot_id,
        kind=EvidenceKind.derived_metric,
        record_ids=[unknown_rec],
        summary="Unknown record ID",
    )
    with pytest.raises(ValueError, match="references unknown record_id"):
        ctx.register_evidence(ref)


def test_tool_context_register_evidence_cutoff_violation_rejected():
    snap = _make_snapshot()
    ctx = ToolContext(snap)
    ref = EvidenceRef(
        evidence_id=str(uuid.uuid4()),
        snapshot_id=snap.snapshot_id,
        kind=EvidenceKind.event,
        record_ids=[],
        observed_at=snap.cutoff_at + timedelta(seconds=5),
        summary="Late event",
    )
    with pytest.raises(ValueError, match="exceeds cutoff"):
        ctx.register_evidence(ref)


def test_tool_context_caching():
    snap = _make_snapshot()
    ctx = ToolContext(snap)

    res1 = get_excursion_summary(ctx)
    res2 = get_excursion_summary(ctx)
    assert res1 is res2

    pol1 = get_handling_policy(ctx)
    pol2 = get_handling_policy(ctx)
    assert pol1 is pol2


# ── Excursion Summary Tests ─────────────────────────────────────────────────


def test_get_excursion_summary_metrics_and_evidence():
    snap = _make_snapshot()
    ctx = ToolContext(snap)
    summary = get_excursion_summary(ctx)

    assert summary["has_excursion"] is True
    assert len(summary["sensors"]) == 2
    assert len(summary["evidence_ids"]) == 2

    for s in summary["sensors"]:
        assert uuid.UUID(s["evidence_id"])
        ev = ctx.get_evidence(s["evidence_id"])
        assert ev is not None
        assert ev.kind == EvidenceKind.derived_metric
        assert ev.method_version == TOOLS_VERSION
        assert ev.snapshot_id == snap.snapshot_id

    # Reference sensor had excursion
    ref_s = next(s for s in summary["sensors"] if s["role"] == "reference")
    assert ref_s["excursion_detected"] is True
    assert ref_s["estimated_out_of_range_seconds"] == 30.0
    assert ref_s["observed_max_c"] == 9.0

    # Comparison sensor had no excursion
    cmp_s = next(s for s in summary["sensors"] if s["role"] == "comparison")
    assert cmp_s["excursion_detected"] is False
    assert cmp_s["estimated_out_of_range_seconds"] == 0.0


def test_get_excursion_summary_zero_readings_honest():
    ref_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.ref"))
    cmp_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.cmp"))
    sensors = [
        Sensor(sensor_id=ref_id, placement="center", role=SensorRole.reference),
        Sensor(sensor_id=cmp_id, placement="door", role=SensorRole.comparison),
    ]
    # Only readings for reference sensor
    readings = [
        Reading(
            event_id=str(uuid.uuid4()),
            sensor_id=ref_id,
            observed_at=_ts(0),
            temperature_c=5.0,
        ),
        Reading(
            event_id=str(uuid.uuid4()),
            sensor_id=ref_id,
            observed_at=_ts(60),
            temperature_c=5.0,
        ),
    ]
    snap = _make_snapshot(readings=readings, sensors=sensors, events=[])
    ctx = ToolContext(snap)
    summary = get_excursion_summary(ctx)

    cmp_s = next(s for s in summary["sensors"] if s["role"] == "comparison")
    assert cmp_s["sample_count"] == 0
    assert cmp_s["coverage_status"] == "insufficient"

    ev = ctx.get_evidence(cmp_s["evidence_id"])
    assert ev is not None
    assert "no readings observed" in ev.summary
    assert ev.record_ids == []


# ── Sensor Comparison Tests ─────────────────────────────────────────────────


def test_get_sensor_comparison_aligned_readings():
    snap = _make_snapshot()
    ctx = ToolContext(snap)
    res = get_sensor_comparison(ctx)

    assert res["count"] == 1
    comp = res["comparisons"][0]
    assert comp["reference_sensor_id"] == snap.sensors[0].sensor_id
    assert comp["comparison_sensor_id"] == snap.sensors[1].sensor_id
    assert comp["missing_comparison_data"] is False
    assert len(comp["aligned_readings"]) == 3
    assert comp["max_difference_c"] == 3.5  # 9.0 vs 5.5 at t=60
    assert comp["disagreement_detected"] is True  # 3.5 > 1.5 threshold

    ev = ctx.get_evidence(comp["evidence_id"])
    assert ev is not None
    assert ev.kind == EvidenceKind.derived_metric
    assert ev.method_version == TOOLS_VERSION
    assert len(ev.record_ids) > 0


def test_get_sensor_comparison_gaps_not_aligned_across():
    ref_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.ref"))
    cmp_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.cmp"))
    sensors = [
        Sensor(sensor_id=ref_id, placement="center", role=SensorRole.reference),
        Sensor(sensor_id=cmp_id, placement="door", role=SensorRole.comparison),
    ]
    # ref at 0s, cmp at 300s (gap > 120s max_gap)
    readings = [
        Reading(
            event_id=str(uuid.uuid4()),
            sensor_id=ref_id,
            observed_at=_ts(0),
            temperature_c=5.0,
        ),
        Reading(
            event_id=str(uuid.uuid4()),
            sensor_id=cmp_id,
            observed_at=_ts(300),
            temperature_c=5.0,
        ),
    ]
    snap = _make_snapshot(readings=readings, sensors=sensors, events=[])
    ctx = ToolContext(snap)
    res = get_sensor_comparison(ctx)

    comp = res["comparisons"][0]
    assert comp["missing_comparison_data"] is True
    assert comp["unaligned_gaps"] is True
    assert len(comp["aligned_readings"]) == 0
    assert comp["max_difference_c"] is None


def test_get_sensor_comparison_zero_readings():
    ref_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.ref"))
    cmp_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.cmp"))
    sensors = [
        Sensor(sensor_id=ref_id, placement="center", role=SensorRole.reference),
        Sensor(sensor_id=cmp_id, placement="door", role=SensorRole.comparison),
    ]
    readings = [
        Reading(
            event_id=str(uuid.uuid4()),
            sensor_id=ref_id,
            observed_at=_ts(0),
            temperature_c=5.0,
        ),
    ]
    snap = _make_snapshot(readings=readings, sensors=sensors, events=[])
    ctx = ToolContext(snap)
    res = get_sensor_comparison(ctx)

    comp = res["comparisons"][0]
    assert comp["missing_comparison_data"] is True
    assert comp["aligned_readings"] == []
    assert comp["max_difference_c"] is None
    ev = ctx.get_evidence(comp["evidence_id"])
    assert ev is not None
    assert "insufficient data" in ev.summary


def test_get_sensor_comparison_no_reference_raises():
    snap = _make_snapshot()
    # Modify sensors to have no reference
    for s in snap.sensors:
        object.__setattr__(s, "role", SensorRole.comparison)
    ctx = ToolContext(snap)
    with pytest.raises(ValueError, match="Snapshot does not contain a configured reference sensor"):
        get_sensor_comparison(ctx)


# ── Event Tool Tests (Door, Refrigeration, Vehicle) ─────────────────────────


def test_get_door_events_observed():
    snap = _make_snapshot()
    ctx = ToolContext(snap)
    res = get_door_events(ctx)

    assert res["is_missing"] is False
    assert res["count"] == 2
    assert len(res["events"]) == 2
    values = [e["value"] for e in res["events"]]
    assert values == ["open", "closed"]

    for ev_id in [e["evidence_id"] for e in res["events"]]:
        ev = ctx.get_evidence(ev_id)
        assert ev is not None
        assert ev.kind == EvidenceKind.event
        assert ev.snapshot_id == snap.snapshot_id

    # Verify deterministic temporal facts
    tf = res.get("temporal_facts")
    assert tf is not None
    assert tf["door_opened_before_rise"] is True
    assert tf["evidence_id"] in res["evidence_ids"]
    tf_ev = ctx.get_evidence(tf["evidence_id"])
    assert tf_ev is not None
    assert tf_ev.kind == EvidenceKind.derived_metric
    assert "does not establish causation" in tf["summary"]


def test_get_door_events_missing_never_assumes_closed():
    # Snapshot with NO door events
    events = [
        Event(
            event_id=str(uuid.uuid4()),
            observed_at=_ts(10),
            event_type=EventType.refrigeration_state,
            value="running",
            source="telemetry",
        )
    ]
    snap = _make_snapshot(events=events)
    ctx = ToolContext(snap)
    res = get_door_events(ctx)

    assert res["is_missing"] is True
    assert res["count"] == 0
    assert res["events"] == []
    assert res["status"] == "missing_door_events"
    assert "No door events" in res["summary"]
    # Never assumes closed!
    assert "closed" not in [e.get("value") for e in res["events"]]


def test_get_refrigeration_events_observed():
    snap = _make_snapshot()
    ctx = ToolContext(snap)
    res = get_refrigeration_events(ctx)

    assert res["is_missing"] is False
    assert res["count"] == 1
    assert res["events"][0]["value"] == "running"
    assert res["has_fault_or_stopped"] is False


def test_get_refrigeration_events_fault_or_stopped_flagged():
    events = [
        Event(
            event_id=str(uuid.uuid4()),
            observed_at=_ts(20),
            event_type=EventType.refrigeration_state,
            value="fault",
            source="telemetry",
        )
    ]
    snap = _make_snapshot(events=events)
    ctx = ToolContext(snap)
    res = get_refrigeration_events(ctx)

    assert res["is_missing"] is False
    assert res["has_fault_or_stopped"] is True


def test_get_refrigeration_events_missing_never_assumes_running():
    # Snapshot with NO refrigeration events
    snap = _make_snapshot(events=[])
    ctx = ToolContext(snap)
    res = get_refrigeration_events(ctx)

    assert res["is_missing"] is True
    assert res["count"] == 0
    assert res["events"] == []
    assert res["status"] == "missing_refrigeration_events"
    assert res["has_fault_or_stopped"] is False
    # Never assumes running!
    assert "running" not in [e.get("value") for e in res["events"]]


def test_get_vehicle_events_observed_and_missing():
    snap = _make_snapshot()
    ctx = ToolContext(snap)
    res = get_vehicle_events(ctx)

    assert res["is_missing"] is False
    assert res["count"] == 1
    assert res["events"][0]["value"] == "stopped"

    # Now with empty events
    empty_snap = _make_snapshot(events=[])
    empty_ctx = ToolContext(empty_snap)
    empty_res = get_vehicle_events(empty_ctx)
    assert empty_res["is_missing"] is True
    assert empty_res["count"] == 0
    assert empty_res["events"] == []


# ── Handling Policy Tests ───────────────────────────────────────────────────


def test_get_handling_policy():
    snap = _make_snapshot()
    ctx = ToolContext(snap)
    pol = get_handling_policy(ctx)

    assert pol["policy_id"] == "policy-001"
    assert pol["policy_version"] == "1.0"
    assert pol["min_c"] == 2.0
    assert pol["max_c"] == 8.0
    assert pol["expected_interval_seconds"] == 60.0
    assert pol["max_gap_seconds"] == 120.0

    ev = ctx.get_evidence(pol["evidence_id"])
    assert ev is not None
    assert ev.kind == EvidenceKind.policy
    assert ev.record_ids == []
    assert "Policy policy-001" in ev.summary


# ── Integration with Canonical Report & Door Snapshot ───────────────────────


def test_integration_with_door_snapshot_and_canonical_report():
    door_path = Path("contracts/examples/door-snapshot.json")
    assert door_path.is_file()
    snap_data = json.loads(door_path.read_text(encoding="utf-8"))
    snapshot = Snapshot.model_validate(snap_data)

    ctx = ToolContext(snapshot)

    # Execute all evidence tools
    excursion_res = get_excursion_summary(ctx)
    comparison_res = get_sensor_comparison(ctx)
    door_res = get_door_events(ctx)
    refrig_res = get_refrigeration_events(ctx)
    veh_res = get_vehicle_events(ctx)
    policy_res = get_handling_policy(ctx)

    assert excursion_res["has_excursion"] is True
    assert door_res["count"] >= 2
    assert comparison_res["count"] >= 1
    assert refrig_res["count"] >= 0
    assert veh_res["count"] >= 0
    assert "policy_id" in policy_res

    all_evidence = ctx.all_evidence()
    assert len(all_evidence) > 0

    # Build a valid canonical Report containing all registered evidence
    valid_ids = {e.evidence_id for e in all_evidence}
    door_ev_ids = [e["evidence_id"] for e in door_res["events"]]
    meas_ev_ids = [s["evidence_id"] for s in excursion_res["sensors"]]
    for eid in door_ev_ids + meas_ev_ids:
        assert eid in valid_ids

    report = Report(
        report_id=str(uuid.uuid4()),
        run_id=str(uuid.uuid4()),
        snapshot_id=snapshot.snapshot_id,
        snapshot_sha256="1dbe7dc1667e3e5eccb954402eeee1a6b06237001bc82ff6953459d8b3b6c8ea",
        schema_version="1.0",
        detector_version=TOOLS_VERSION,
        prompt_version="1.0",
        model_id="claude-3-5-sonnet",
        created_at=datetime(2026, 1, 1, 14, 0, 0, tzinfo=UTC),
        cutoff_at=snapshot.cutoff_at,
        measurements=ctx.measurements,
        outcome=Outcome.hypothesis_supported,
        primary_hypothesis=HypothesisType.door_exposure,
        hypotheses=[
            Hypothesis(
                hypothesis=HypothesisType.door_exposure,
                assessment=Assessment.supported,
                supporting_evidence_ids=door_ev_ids,
                conflicting_evidence_ids=[],
                missing_evidence=[],
                explanation="Door opened during excursion",
            )
        ],
        next_checks=[
            NextCheck(
                code=NextCheckCode.inspect_door,
                reason="Check door seals",
                related_evidence_ids=door_ev_ids,
            )
        ],
        limitations=["Simulated shipment data"],
        verification=Verification(
            status=VerificationStatus.passed,
            errors=[],
            warnings=[],
        ),
        generation_mode=GenerationMode.bedrock,
        review_required=True,
        evidence=all_evidence,
    )

    # Validate provenance passes cleanly
    assert report.validate_evidence_provenance() is report
    dumped = report.model_dump(mode="json")
    assert isinstance(dumped, dict)


# ── Blocker Regression Tests ────────────────────────────────────────────────


def test_fabricated_measurement_rejected():
    """ToolContext rejects caller measurement conflicting with deterministic detector."""
    snap = _make_snapshot()
    real_measurements = detect_excursions(snap, snap.policy)
    ref_m = next(m for m in real_measurements if m.sensor_id == snap.sensors[0].sensor_id)
    cmp_m = next(m for m in real_measurements if m.sensor_id == snap.sensors[1].sensor_id)

    # Fabricate 9,999 seconds out of range
    fabricated_ref = ref_m.model_copy(update={"estimated_out_of_range_seconds": 9999.0})
    with pytest.raises(ValueError, match="does not match deterministic detector result"):
        ToolContext(snap, measurements=[fabricated_ref, cmp_m])


def test_unknown_sensor_in_measurements_rejected():
    """ToolContext rejects caller-supplied measurements with unknown sensor IDs cleanly."""
    snap = _make_snapshot()
    real_measurements = detect_excursions(snap, snap.policy)
    ref_m = real_measurements[0]
    unknown_m = ref_m.model_copy(update={"sensor_id": str(uuid.uuid4())})
    with pytest.raises(ValueError, match="references unknown sensor"):
        ToolContext(snap, measurements=[unknown_m, real_measurements[1]])


def test_100_second_offset_not_aligned_no_false_disagreement():
    """100s offset between sensors is not aligned and does not report false disagreement."""
    ref_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.ref"))
    cmp_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.cmp"))
    sensors = [
        Sensor(sensor_id=ref_id, placement="center", role=SensorRole.reference),
        Sensor(sensor_id=cmp_id, placement="door", role=SensorRole.comparison),
    ]
    # Ref reading at second 0 (5°C), comparison reading at second 100 (9°C)
    readings = [
        Reading(
            event_id=str(uuid.uuid4()),
            sensor_id=ref_id,
            observed_at=_ts(0),
            temperature_c=5.0,
        ),
        Reading(
            event_id=str(uuid.uuid4()),
            sensor_id=cmp_id,
            observed_at=_ts(100),
            temperature_c=9.0,
        ),
    ]
    snap = _make_snapshot(readings=readings, sensors=sensors, events=[])
    ctx = ToolContext(snap)
    res = get_sensor_comparison(ctx)

    assert res["count"] == 1
    comp = res["comparisons"][0]
    # Must NOT report 4°C disagreement
    assert comp["disagreement_detected"] is False
    assert comp["max_difference_c"] is None
    assert comp["missing_comparison_data"] is True
    assert comp["unaligned_gaps"] is True
    assert comp["aligned_readings"] == []
    assert comp["aligned_count"] == 0

    ev = ctx.get_evidence(comp["evidence_id"])
    assert ev is not None
    assert ev.record_ids == []
    assert ev.interval is None
    assert "insufficient aligned data" in ev.summary


def test_evidence_interval_uses_only_actually_aligned_readings():
    """EvidenceInterval and record_ids must describe only readings actually used in alignment."""
    ref_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.ref"))
    cmp_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.cmp"))
    sensors = [
        Sensor(sensor_id=ref_id, placement="center", role=SensorRole.reference),
        Sensor(sensor_id=cmp_id, placement="door", role=SensorRole.comparison),
    ]
    r0 = Reading(
        event_id=str(uuid.uuid4()),
        sensor_id=ref_id,
        observed_at=_ts(0),
        temperature_c=5.0,
    )
    r60 = Reading(
        event_id=str(uuid.uuid4()),
        sensor_id=ref_id,
        observed_at=_ts(60),
        temperature_c=5.0,
    )
    c0 = Reading(
        event_id=str(uuid.uuid4()),
        sensor_id=cmp_id,
        observed_at=_ts(0),
        temperature_c=5.2,
    )
    c60 = Reading(
        event_id=str(uuid.uuid4()),
        sensor_id=cmp_id,
        observed_at=_ts(60),
        temperature_c=5.3,
    )
    # Unaligned reading 300s later
    c300 = Reading(
        event_id=str(uuid.uuid4()),
        sensor_id=cmp_id,
        observed_at=_ts(300),
        temperature_c=5.0,
    )
    snap = _make_snapshot(readings=[r0, r60, c0, c60, c300], sensors=sensors, events=[])
    ctx = ToolContext(snap)
    res = get_sensor_comparison(ctx)

    comp = res["comparisons"][0]
    assert comp["aligned_count"] == 2
    ev = ctx.get_evidence(comp["evidence_id"])
    assert ev is not None
    # Interval must strictly span [0s, 60s], NOT [0s, 300s]
    assert ev.interval is not None
    assert ev.interval.start_at == _ts(0)
    assert ev.interval.end_at == _ts(60)
    # Record IDs must contain only r0, r60, c0, c60, NOT c300
    assert set(ev.record_ids) == {r0.event_id, r60.event_id, c0.event_id, c60.event_id}
    assert c300.event_id not in ev.record_ids


def test_output_bounding_aligned_readings():
    """get_sensor_comparison bounds aligned_readings size while preserving count and evidence."""
    ref_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.ref"))
    cmp_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.cmp"))
    sensors = [
        Sensor(sensor_id=ref_id, placement="center", role=SensorRole.reference),
        Sensor(sensor_id=cmp_id, placement="door", role=SensorRole.comparison),
    ]
    # Create 50 aligned readings (every 60s)
    readings: list[Reading] = []
    for i in range(50):
        t = _ts(i * 60)
        readings.append(
            Reading(
                event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"r.{i}")),
                sensor_id=ref_id,
                observed_at=t,
                temperature_c=5.0,
            )
        )
        readings.append(
            Reading(
                event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"c.{i}")),
                sensor_id=cmp_id,
                observed_at=t,
                temperature_c=5.2 if i != 25 else 8.5,
            )
        )
    snap = _make_snapshot(readings=readings, sensors=sensors, events=[], cutoff_seconds=3600)
    ctx = ToolContext(snap)
    res = get_sensor_comparison(ctx)

    comp = res["comparisons"][0]
    assert comp["aligned_count"] == 50
    assert comp["is_truncated"] is True
    assert len(comp["aligned_readings"]) <= 10
    # Max disagreement was preserved in bounded list
    assert comp["disagreement_detected"] is True
    assert comp["max_difference_c"] == 3.5

    # Evidence in registry has all 100 reading record IDs
    ev = ctx.get_evidence(comp["evidence_id"])
    assert ev is not None
    assert len(ev.record_ids) == 100


def test_output_bounding_events():
    """_get_events_by_type bounds events size while preserving count and evidence IDs."""
    events: list[Event] = []
    for i in range(30):
        events.append(
            Event(
                event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"door.{i}")),
                observed_at=_ts(i * 10),
                event_type=EventType.door_state,
                value="open" if i % 2 == 0 else "closed",
                source="sensor",
            )
        )
    snap = _make_snapshot(events=events, cutoff_seconds=600)
    ctx = ToolContext(snap)
    res = get_door_events(ctx)

    assert res["count"] == 30
    assert res["is_truncated"] is True
    assert len(res["events"]) <= 10
    # 30 event evidence IDs + 1 door_temporal_facts evidence ID preserved
    tf = res.get("temporal_facts")
    assert tf is not None
    assert tf["evidence_id"] in res["evidence_ids"]
    assert len(res["evidence_ids"]) == 31


def test_door_temporal_facts_opening_overlap_recovery():
    """Door temporal facts accurately compute opening before rise, overlap, and recovery."""
    ref_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.ref"))
    cmp_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.cmp"))
    sensors = [
        Sensor(sensor_id=ref_id, placement="center", role=SensorRole.reference),
        Sensor(sensor_id=cmp_id, placement="door", role=SensorRole.comparison),
    ]
    # Door opens at 30s, closes at 90s
    e_open = Event(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "door.open")),
        observed_at=_ts(30),
        event_type=EventType.door_state,
        value="open",
        source="sensor",
    )
    e_close = Event(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "door.close")),
        observed_at=_ts(90),
        event_type=EventType.door_state,
        value="closed",
        source="sensor",
    )
    # Ref temp: 5°C at 0s, 9°C at 60s (excursion start), 9°C at 90s, 5°C at 120s (recovery)
    r0 = Reading(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "r0")),
        sensor_id=ref_id,
        observed_at=_ts(0),
        temperature_c=5.0,
    )
    r60 = Reading(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "r60")),
        sensor_id=ref_id,
        observed_at=_ts(60),
        temperature_c=9.0,
    )
    r90 = Reading(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "r90")),
        sensor_id=ref_id,
        observed_at=_ts(90),
        temperature_c=9.0,
    )
    r120 = Reading(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "r120")),
        sensor_id=ref_id,
        observed_at=_ts(120),
        temperature_c=5.0,
    )
    snap = _make_snapshot(
        readings=[r0, r60, r90, r120],
        sensors=sensors,
        events=[e_open, e_close],
        cutoff_seconds=300,
    )
    ctx = ToolContext(snap)
    res = get_door_events(ctx)

    tf = res.get("temporal_facts")
    assert tf is not None
    assert tf["has_excursion"] is True
    assert tf["door_opened_before_rise"] is True
    assert tf["lead_time_seconds"] == 15.0  # 45s threshold crossing - 30s open = 15s
    assert tf["overlap_interval"] is not None
    assert tf["overlap_interval"]["duration_seconds"] == 45.0  # [45s, 90s]
    assert tf["temperature_recovered_after_close"] is True
    assert tf["recovery_time_seconds"] == 7.5  # 97.5s recovery - 90s close = 7.5s
    assert tf["method_version"] == TOOLS_VERSION
    assert "does not establish causation" in tf["summary"]

    # Verify registered evidence ref
    ev = ctx.get_evidence(tf["evidence_id"])
    assert ev is not None
    assert ev.kind == EvidenceKind.derived_metric
    assert ev.method_version == TOOLS_VERSION
    assert e_open.event_id in ev.record_ids
    assert e_close.event_id in ev.record_ids
    assert r0.event_id in ev.record_ids
    assert r60.event_id in ev.record_ids
    assert r90.event_id in ev.record_ids
    assert r120.event_id in ev.record_ids


def test_door_temporal_facts_door_opens_after_rise():
    """Door opening after temperature rise is flagged as not opening before rise."""
    ref_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.ref"))
    cmp_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.cmp"))
    sensors = [
        Sensor(sensor_id=ref_id, placement="center", role=SensorRole.reference),
        Sensor(sensor_id=cmp_id, placement="door", role=SensorRole.comparison),
    ]
    # Temperature rises at 30s
    r0 = Reading(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "r0")),
        sensor_id=ref_id,
        observed_at=_ts(0),
        temperature_c=5.0,
    )
    r30 = Reading(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "r30")),
        sensor_id=ref_id,
        observed_at=_ts(30),
        temperature_c=9.0,
    )
    # Door opens at 60s (after rise)
    e_open = Event(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "door.open")),
        observed_at=_ts(60),
        event_type=EventType.door_state,
        value="open",
        source="sensor",
    )
    snap = _make_snapshot(
        readings=[r0, r30],
        sensors=sensors,
        events=[e_open],
        cutoff_seconds=300,
    )
    ctx = ToolContext(snap)
    res = get_door_events(ctx)

    tf = res.get("temporal_facts")
    assert tf is not None
    assert tf["has_excursion"] is True
    assert tf["door_opened_before_rise"] is False
    assert tf["lead_time_seconds"] is None
    assert "Door open event was not observed prior to temperature rise" in tf["summary"]
    assert "does not establish causation" in tf["summary"]


def test_tool_context_normalizes_duplicate_readings_and_events():
    """ToolContext normalizes snapshot via validate_snapshot (dedupes, rejects conflicts)."""
    snap = _make_snapshot()
    r_dup = snap.readings[0].model_copy()
    e_dup = snap.events[0].model_copy()

    # Snapshot with identical duplicates
    snap_with_dups = snap.model_copy(
        update={
            "readings": snap.readings + [r_dup],
            "events": snap.events + [e_dup],
        }
    )
    ctx = ToolContext(snap_with_dups)
    # Deduplication collapses duplicates
    assert len(ctx.snapshot.readings) == len(snap.readings)
    assert len(ctx.snapshot.events) == len(snap.events)
    assert len(ctx._valid_record_ids) == len(snap.readings) + len(snap.events)

    # Conflicting duplicate reading raises ValueError
    r_conflict = r_dup.model_copy(update={"temperature_c": r_dup.temperature_c + 5.0})
    snap_with_conflict = snap.model_copy(update={"readings": snap.readings + [r_conflict]})
    with pytest.raises(ValueError, match="Conflicting duplicate reading"):
        ToolContext(snap_with_conflict)


def test_refrigeration_fault_after_ten_running_events():
    """Refrigeration summary detects fault even when preceded by 10 running events."""
    events = [
        Event(
            event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"ref.{i}")),
            observed_at=_ts(i * 10),
            event_type=EventType.refrigeration_state,
            value="running",
            source="telemetry",
        )
        for i in range(10)
    ]
    events.append(
        Event(
            event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "ref.fault")),
            observed_at=_ts(100),
            event_type=EventType.refrigeration_state,
            value="fault",
            source="telemetry",
        )
    )
    snap = _make_snapshot(events=events, cutoff_seconds=300)
    ctx = ToolContext(snap)
    res = get_refrigeration_events(ctx)

    assert res["count"] == 11
    assert res["is_truncated"] is True
    assert len(res["events"]) == 10
    assert res["has_fault_or_stopped"] is True


def test_door_opened_at_second_50_after_interpolated_rise_at_second_45():
    """Door opening at 50s after threshold crossing at 45s is not reported before rise."""
    ref_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.ref"))
    cmp_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.cmp"))
    sensors = [
        Sensor(sensor_id=ref_id, placement="center", role=SensorRole.reference),
        Sensor(sensor_id=cmp_id, placement="door", role=SensorRole.comparison),
    ]
    # Threshold 8°C. 5°C at 0s, 9°C at 60s. Crossing at 45s.
    r0 = Reading(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "r0")),
        sensor_id=ref_id,
        observed_at=_ts(0),
        temperature_c=5.0,
    )
    r60 = Reading(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "r60")),
        sensor_id=ref_id,
        observed_at=_ts(60),
        temperature_c=9.0,
    )
    # Door opens at second 50 (after rise at 45s, but before sample at 60s)
    e_open = Event(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "door.open")),
        observed_at=_ts(50),
        event_type=EventType.door_state,
        value="open",
        source="sensor",
    )
    snap = _make_snapshot(
        readings=[r0, r60],
        sensors=sensors,
        events=[e_open],
        cutoff_seconds=300,
    )
    ctx = ToolContext(snap)
    res = get_door_events(ctx)

    tf = res.get("temporal_facts")
    assert tf is not None
    assert tf["has_excursion"] is True
    assert tf["door_opened_before_rise"] is False
    assert tf["lead_time_seconds"] is None
    assert "Door open event was not observed prior to temperature rise" in tf["summary"]


def test_door_closed_before_excursion_does_not_report_recovery():
    """Door closing before excursion starts does not report recovery at door close time."""
    ref_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.ref"))
    cmp_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.cmp"))
    sensors = [
        Sensor(sensor_id=ref_id, placement="center", role=SensorRole.reference),
        Sensor(sensor_id=cmp_id, placement="door", role=SensorRole.comparison),
    ]
    # Door opens at 10s, closes at 20s
    e_open = Event(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "door.open")),
        observed_at=_ts(10),
        event_type=EventType.door_state,
        value="open",
        source="sensor",
    )
    e_close = Event(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "door.close")),
        observed_at=_ts(20),
        event_type=EventType.door_state,
        value="closed",
        source="sensor",
    )
    # Temperature stays normal 5°C until 60s, then rises to 9°C at 120s
    r0 = Reading(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "r0")),
        sensor_id=ref_id,
        observed_at=_ts(0),
        temperature_c=5.0,
    )
    r30 = Reading(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "r30")),
        sensor_id=ref_id,
        observed_at=_ts(30),
        temperature_c=5.0,
    )
    r60 = Reading(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "r60")),
        sensor_id=ref_id,
        observed_at=_ts(60),
        temperature_c=5.0,
    )
    r120 = Reading(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "r120")),
        sensor_id=ref_id,
        observed_at=_ts(120),
        temperature_c=9.0,
    )
    snap = _make_snapshot(
        readings=[r0, r30, r60, r120],
        sensors=sensors,
        events=[e_open, e_close],
        cutoff_seconds=300,
    )
    ctx = ToolContext(snap)
    res = get_door_events(ctx)

    tf = res.get("temporal_facts")
    assert tf is not None
    assert tf["has_excursion"] is True
    # Door closed 85s before excursion crossing at 105s; cannot claim recovery
    assert tf["temperature_recovered_after_close"] is False
    assert tf["recovery_time_seconds"] is None
    assert tf["overlap_interval"] is None


def test_door_overlap_not_claimed_across_unobserved_gap():
    """Door opening inside an unobserved gap does not produce door-excursion overlap.

    Two high readings separated by a 300s unobserved gap (> policy max_gap_seconds 120s)
    produce 0s of estimated excursion duration and 300s unknown duration in the deterministic
    detector. A door open from 50s to 150s (100s duration) inside that unobserved gap must NOT
    claim 100s of door-excursion overlap because no excursion duration can be established within
    the unobserved gap. Overlap must be calculated only from observed or validly interpolated
    intervals.
    """
    ref_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.ref"))
    cmp_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "sensor.cmp"))
    sensors = [
        Sensor(sensor_id=ref_id, placement="center", role=SensorRole.reference),
        Sensor(sensor_id=cmp_id, placement="door", role=SensorRole.comparison),
    ]

    # Two high readings separated by 300s (policy default max_gap is 120s)
    r0 = Reading(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "r0")),
        sensor_id=ref_id,
        observed_at=_ts(0),
        temperature_c=10.0,
    )
    r300 = Reading(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "r300")),
        sensor_id=ref_id,
        observed_at=_ts(300),
        temperature_c=10.0,
    )

    # Door opens at 50s and closes at 150s (100s duration inside the 300s unobserved gap)
    e_open = Event(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "door.open")),
        observed_at=_ts(50),
        event_type=EventType.door_state,
        value="open",
        source="sensor",
    )
    e_close = Event(
        event_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "door.close")),
        observed_at=_ts(150),
        event_type=EventType.door_state,
        value="closed",
        source="sensor",
    )

    snap = _make_snapshot(
        readings=[r0, r300],
        sensors=sensors,
        events=[e_open, e_close],
        cutoff_seconds=300,
    )
    ctx = ToolContext(snap)

    # Verify deterministic detector metrics on the reference sensor:
    # 0s estimated excursion, 300s unknown duration
    ref_m = next(m for m in ctx.measurements if m.sensor_id == ref_id)
    assert ref_m.estimated_out_of_range_seconds == 0.0
    assert ref_m.unknown_duration_seconds == 300.0

    res = get_door_events(ctx)
    tf = res.get("temporal_facts")
    assert tf is not None
    assert tf["has_excursion"] is True
    # Door opened after temperature was already out of range at 0s
    assert tf["door_opened_before_rise"] is False
    assert tf["lead_time_seconds"] is None
    # Crucial assertion: overlap must NOT be claimed inside the unobserved gap
    assert tf["overlap_interval"] is None
    # No recovery can be established across unobserved gap
    assert tf["temperature_recovered_after_close"] is False
    assert tf["recovery_time_seconds"] is None
    assert "No overlap observed between open door and excursion window" in tf["summary"]




