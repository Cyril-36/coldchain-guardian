"""Tests for the investigation agent."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from coldchain.contracts.enums import (
    EventType,
    GenerationMode,
    HypothesisType,
    Outcome,
    SensorRole,
    VerificationStatus,
)
from coldchain.contracts.schemas import (
    Event,
    Policy,
    Reading,
    Sensor,
    Snapshot,
)
from coldchain.investigation.agent import investigate

BASE_TS = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

POLICY = Policy(
    policy_id="pol-1",
    policy_version="1.0",
    min_c=2.0,
    max_c=8.0,
    expected_interval_seconds=60.0,
    max_gap_seconds=120.0,
)


def _ts(seconds: int) -> datetime:
    return BASE_TS + timedelta(seconds=seconds)


def _reading(event_id: str, sensor_id: str, seconds: int, temp: float) -> Reading:
    return Reading(
        event_id=event_id,
        sensor_id=sensor_id,
        observed_at=_ts(seconds),
        temperature_c=temp,
    )


def _make_normal_snapshot() -> Snapshot:
    """All readings in range — no excursion."""
    return Snapshot(
        snapshot_id=str(uuid.uuid4()),
        shipment_id=str(uuid.uuid4()),
        cutoff_at=_ts(600),
        policy=POLICY,
        sensors=[
            Sensor(sensor_id="s-ref", placement="center", role=SensorRole.reference),
        ],
        readings=[
            _reading("r-0", "s-ref", 0, 5.0),
            _reading("r-1", "s-ref", 60, 6.0),
            _reading("r-2", "s-ref", 120, 5.0),
        ],
        events=[],
    )


def _make_door_excursion_snapshot() -> Snapshot:
    """Excursion with door-open event — triggers fake model door_exposure."""
    return Snapshot(
        snapshot_id=str(uuid.uuid4()),
        shipment_id=str(uuid.uuid4()),
        cutoff_at=_ts(600),
        policy=POLICY,
        sensors=[
            Sensor(sensor_id="s-ref", placement="center", role=SensorRole.reference),
            Sensor(sensor_id="s-cmp", placement="door", role=SensorRole.comparison),
        ],
        readings=[
            # Reference — excursion
            _reading("r-0", "s-ref", 0, 5.0),
            _reading("r-1", "s-ref", 60, 9.0),
            _reading("r-2", "s-ref", 120, 9.0),
            _reading("r-3", "s-ref", 180, 5.0),
            # Comparison — normal
            _reading("c-0", "s-cmp", 0, 5.0),
            _reading("c-1", "s-cmp", 60, 5.0),
            _reading("c-2", "s-cmp", 120, 5.0),
            _reading("c-3", "s-cmp", 180, 5.0),
        ],
        events=[
            Event(
                event_id="e-door-1",
                observed_at=_ts(30),
                event_type=EventType.door_state,
                value="open",
                source="sensor",
            ),
            Event(
                event_id="e-door-2",
                observed_at=_ts(150),
                event_type=EventType.door_state,
                value="closed",
                source="sensor",
            ),
        ],
    )


def _make_multi_window_snapshot() -> Snapshot:
    """Excursion with a gap — triggers multiple_windows_unsupported."""
    return Snapshot(
        snapshot_id=str(uuid.uuid4()),
        shipment_id=str(uuid.uuid4()),
        cutoff_at=_ts(600),
        policy=POLICY,
        sensors=[
            Sensor(sensor_id="s-ref", placement="center", role=SensorRole.reference),
        ],
        readings=[
            _reading("r-0", "s-ref", 0, 10.0),     # out of range
            _reading("r-1", "s-ref", 300, 10.0),    # gap > 120s, still out of range
        ],
        events=[],
    )


# ── Tests ───────────────────────────────────────────────────────────────────


def test_no_excursion_skips_model():
    """All readings in range → no_excursion report, model not called."""
    snapshot = _make_normal_snapshot()

    with patch(
        "coldchain.investigation.agent.fake_investigate",
        wraps=lambda x: pytest.fail("Model should not be called"),
    ) as mock_model:
        report = investigate(snapshot, run_id="run-normal")

    assert report.outcome == Outcome.no_excursion
    assert report.review_required is False
    assert report.generation_mode == GenerationMode.deterministic_only
    assert len(report.hypotheses) == 0
    mock_model.assert_not_called()


def test_door_scenario_with_fake_model():
    """Snapshot with door open + excursion → hypothesis_supported, door_exposure."""
    snapshot = _make_door_excursion_snapshot()
    report = investigate(snapshot, run_id="run-door")

    assert report.outcome == Outcome.hypothesis_supported
    assert report.primary_hypothesis == HypothesisType.door_exposure
    assert report.generation_mode == GenerationMode.deterministic_only
    assert report.review_required is True

    # Should have hypotheses from fake model
    door_hyps = [
        h for h in report.hypotheses
        if h.hypothesis == HypothesisType.door_exposure
    ]
    assert len(door_hyps) == 1
    assert door_hyps[0].assessment.value == "supported"


def test_verification_failure_returns_needs_review():
    """Pass invalid evidence IDs → report marked needs_review."""
    snapshot = _make_door_excursion_snapshot()

    # Fake model returns bogus evidence IDs that won't pass verification
    def _bad_model(tool_results: dict) -> dict:
        return {
            "outcome": Outcome.hypothesis_supported,
            "primary_hypothesis": HypothesisType.door_exposure,
            "hypotheses": [
                {
                    "hypothesis": HypothesisType.door_exposure,
                    "assessment": "supported",
                    "supporting_evidence_ids": ["ev-bogus-9999"],
                    "conflicting_evidence_ids": [],
                    "missing_evidence": [],
                    "explanation": "Fake evidence.",
                },
            ],
            "next_checks": [],
            "limitations": ["Simulated data"],
        }

    with patch("coldchain.investigation.agent.fake_investigate", side_effect=_bad_model):
        report = investigate(snapshot, run_id="run-bad-evidence")

    assert report.review_required is True
    assert report.verification.status == VerificationStatus.blocked
    assert any("Nonexistent" in e for e in report.verification.errors)


def test_multiple_windows_returns_needs_review():
    """Snapshot triggering multiple windows → needs_review."""
    snapshot = _make_multi_window_snapshot()
    report = investigate(snapshot, run_id="run-multi")

    assert report.review_required is True
    assert report.outcome == Outcome.unresolved
    assert any("multiple" in e.lower() or "Multiple" in e for e in report.verification.errors + report.limitations)
