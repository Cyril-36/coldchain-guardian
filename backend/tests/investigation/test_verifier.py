"""Tests for the deterministic report verifier."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from coldchain.contracts.enums import (
    Assessment,
    GenerationMode,
    HypothesisType,
    Outcome,
    SensorRole,
    VerificationStatus,
)
from coldchain.contracts.schemas import (
    Event,
    Hypothesis,
    Policy,
    Reading,
    Report,
    Sensor,
    SensorMeasurement,
    Snapshot,
    Verification,
)
from coldchain.investigation.tools import (
    ToolContext,
    get_door_events,
    get_excursion_summary,
    get_handling_policy,
    get_refrigeration_events,
    get_sensor_comparison,
)
from coldchain.investigation.verifier import verify_report
from coldchain.investigation.agent import investigate

BASE_TS = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _ts(seconds: int) -> datetime:
    return BASE_TS + timedelta(seconds=seconds)


def _make_snapshot_and_ctx():
    """Build a snapshot, ToolContext, and measurements for verifier tests."""
    sensors = [
        Sensor(sensor_id="s-ref", placement="center", role=SensorRole.reference),
    ]
    readings = [
        Reading(event_id="r-0", sensor_id="s-ref", observed_at=_ts(0), temperature_c=5.0),
        Reading(event_id="r-1", sensor_id="s-ref", observed_at=_ts(60), temperature_c=9.0),
        Reading(event_id="r-2", sensor_id="s-ref", observed_at=_ts(120), temperature_c=5.0),
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
        events=[
            Event(
                event_id="e-1",
                observed_at=_ts(30),
                event_type="door_state",
                value="open",
                source="sensor",
            ),
        ],
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
    ]
    ctx = ToolContext(snapshot=snapshot, measurements=measurements, run_id="run-001")
    # Populate evidence registry
    get_excursion_summary(ctx)
    get_door_events(ctx)
    get_handling_policy(ctx)
    return snapshot, ctx, measurements


def _build_report(
    measurements: list[SensorMeasurement],
    ctx: ToolContext,
    **overrides,
) -> Report:
    """Build a minimal valid Report."""
    evidence_ids = list(ctx.all_evidence_ids())
    defaults = {
        "report_id": str(uuid.uuid4()),
        "run_id": "run-001",
        "snapshot_id": "snap-001",
        "snapshot_sha256": "a" * 64,
        "detector_version": "1.0.0",
        "created_at": BASE_TS,
        "cutoff_at": _ts(600),
        "measurements": measurements,
        "outcome": Outcome.hypothesis_supported,
        "primary_hypothesis": HypothesisType.door_exposure,
        "hypotheses": [
            Hypothesis(
                hypothesis=HypothesisType.door_exposure,
                assessment=Assessment.supported,
                supporting_evidence_ids=evidence_ids[:2] if len(evidence_ids) >= 2 else evidence_ids,
                explanation="Door opened during excursion.",
            ),
        ],
        "verification": Verification(status=VerificationStatus.passed),
        "generation_mode": GenerationMode.deterministic_only,
        "review_required": True,
        "limitations": ["Simulated data"],
    }
    defaults.update(overrides)
    return Report(**defaults)


# ── Tests ───────────────────────────────────────────────────────────────────


def test_valid_report_passes():
    snapshot, ctx, measurements = _make_snapshot_and_ctx()
    report = _build_report(measurements, ctx)
    verification = verify_report(report, ctx, measurements)

    assert verification.status == VerificationStatus.passed
    assert len(verification.errors) == 0


def test_nonexistent_evidence_id_blocked():
    snapshot, ctx, measurements = _make_snapshot_and_ctx()
    report = _build_report(
        measurements,
        ctx,
        hypotheses=[
            Hypothesis(
                hypothesis=HypothesisType.door_exposure,
                assessment=Assessment.supported,
                supporting_evidence_ids=["ev-does-not-exist"],
                explanation="Door opened during excursion.",
            ),
        ],
    )
    verification = verify_report(report, ctx, measurements)

    assert verification.status == VerificationStatus.blocked
    assert any("Nonexistent" in e for e in verification.errors)


def test_numeric_mismatch_blocked():
    snapshot, ctx, measurements = _make_snapshot_and_ctx()
    # Tamper with measurement values in the report
    tampered_measurements = [
        SensorMeasurement(
            sensor_id="s-ref",
            role=SensorRole.reference,
            excursion_detected=True,
            estimated_out_of_range_seconds=999.0,  # wrong value
            sample_count=3,
            observed_min_c=5.0,
            observed_max_c=9.0,
            evidence_ids=["ev-det-0"],
        ),
    ]
    report = _build_report(tampered_measurements, ctx)
    verification = verify_report(report, ctx, measurements)

    assert verification.status == VerificationStatus.blocked
    assert any("estimated_out_of_range_seconds" in e for e in verification.errors)


def test_prohibited_phrase_blocked():
    snapshot, ctx, measurements = _make_snapshot_and_ctx()
    evidence_ids = list(ctx.all_evidence_ids())
    report = _build_report(
        measurements,
        ctx,
        hypotheses=[
            Hypothesis(
                hypothesis=HypothesisType.door_exposure,
                assessment=Assessment.supported,
                supporting_evidence_ids=evidence_ids[:2] if len(evidence_ids) >= 2 else evidence_ids,
                explanation="The product is safe to use after this excursion.",
            ),
        ],
    )
    verification = verify_report(report, ctx, measurements)

    assert verification.status == VerificationStatus.blocked
    assert any("safe to use" in e for e in verification.errors)


def test_no_excursion_with_hypotheses_blocked():
    snapshot, ctx, measurements = _make_snapshot_and_ctx()
    report = _build_report(
        measurements,
        ctx,
        outcome=Outcome.no_excursion,
        primary_hypothesis=None,
        hypotheses=[
            Hypothesis(
                hypothesis=HypothesisType.door_exposure,
                assessment=Assessment.insufficient,
                explanation="Should not be here with no_excursion.",
            ),
        ],
    )
    verification = verify_report(report, ctx, measurements)

    assert verification.status == VerificationStatus.blocked
    assert any("no_excursion" in e for e in verification.errors)


def test_supported_without_primary_blocked():
    snapshot, ctx, measurements = _make_snapshot_and_ctx()
    evidence_ids = list(ctx.all_evidence_ids())
    report = _build_report(
        measurements,
        ctx,
        outcome=Outcome.hypothesis_supported,
        primary_hypothesis=None,  # missing
        hypotheses=[
            Hypothesis(
                hypothesis=HypothesisType.door_exposure,
                assessment=Assessment.supported,
                supporting_evidence_ids=evidence_ids[:2] if len(evidence_ids) >= 2 else evidence_ids,
                explanation="Door opened.",
            ),
        ],
    )
    verification = verify_report(report, ctx, measurements)

    assert verification.status == VerificationStatus.blocked
    assert any("primary_hypothesis" in e for e in verification.errors)


def test_model_failure_deterministic_fallback():
    """investigate() with model exception returns needs_review report."""
    sensors = [
        Sensor(sensor_id="s-ref", placement="center", role=SensorRole.reference),
    ]
    readings = [
        Reading(event_id="r-0", sensor_id="s-ref", observed_at=_ts(0), temperature_c=5.0),
        Reading(event_id="r-1", sensor_id="s-ref", observed_at=_ts(60), temperature_c=9.0),
        Reading(event_id="r-2", sensor_id="s-ref", observed_at=_ts(120), temperature_c=5.0),
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
        events=[],
    )

    with patch(
        "coldchain.investigation.agent.fake_investigate",
        side_effect=RuntimeError("model exploded"),
    ):
        report = investigate(snapshot, run_id="run-fail")

    assert report.review_required is True
    assert report.outcome == Outcome.unresolved
    assert report.verification.status == VerificationStatus.blocked
    assert any("model exploded" in e for e in report.verification.errors)
