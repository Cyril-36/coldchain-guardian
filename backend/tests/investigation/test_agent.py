"""Report behavior at the boundary between model choices and measured evidence."""

from datetime import UTC, datetime
from uuid import uuid4

from coldchain.contracts import Snapshot
from coldchain.contracts.enums import Assessment, EventType, HypothesisType, Outcome
from coldchain.contracts.schemas import Event
from coldchain.investigation.agent import investigate
from coldchain.investigation.tools import get_door_events, get_excursion_summary
from coldchain.investigation.verifier import InvestigationProposal, ProposedHypothesis
from coldchain.simulator import generate_snapshot

BASE = datetime(2026, 9, 18, tzinfo=UTC)


def _snapshot(scenario: str) -> Snapshot:
    return Snapshot.model_validate(generate_snapshot(scenario, 17, BASE))


def _door_proposal(ctx) -> InvestigationProposal:
    summary = get_excursion_summary(ctx)
    door = get_door_events(ctx)
    reference = next(
        item["evidence_id"]
        for item in summary["sensors"]
        if item["role"] == "reference" and item["excursion_detected"]
    )
    opening = next(item["evidence_id"] for item in door["events"] if item["value"] == "open")
    temporal = door["temporal_facts"]["evidence_id"]
    return InvestigationProposal(
        outcome=Outcome.hypothesis_supported,
        primary_hypothesis=HypothesisType.door_exposure,
        hypotheses=[
            ProposedHypothesis(
                hypothesis=HypothesisType.door_exposure,
                assessment=Assessment.supported,
                supporting_evidence_ids=[reference, opening, temporal],
            )
        ],
    )


def test_complete_normal_run_never_calls_model() -> None:
    calls = 0

    def proposer(_ctx):
        nonlocal calls
        calls += 1
        raise AssertionError("normal control must not invoke Bedrock")

    report = investigate(_snapshot("normal_control"), str(uuid4()), proposer)

    assert calls == 0
    assert report.outcome == Outcome.no_excursion
    assert report.verification.status == "passed"
    assert report.review_required is False
    assert report.generation_mode == "deterministic_only"


def test_cited_door_proposal_passes_without_model_authored_numbers() -> None:
    report = investigate(
        _snapshot("door_exposure"), str(uuid4()), _door_proposal, model_id="test-model"
    )

    assert report.outcome == Outcome.hypothesis_supported
    assert report.primary_hypothesis == HypothesisType.door_exposure
    assert report.verification.status == "passed"
    assert report.generation_mode == "bedrock"
    assert report.measurements[0].estimated_out_of_range_seconds > 0
    assert "causation is not proven" in report.hypotheses[0].explanation


def test_unknown_evidence_blocks_model_proposal_and_hides_its_claim() -> None:
    def invented(ctx):
        proposal = _door_proposal(ctx)
        proposal.hypotheses[0].supporting_evidence_ids[0] = str(uuid4())
        return proposal

    report = investigate(_snapshot("door_exposure"), str(uuid4()), invented, model_id="model")

    assert report.outcome == Outcome.unresolved
    assert report.verification.status == "blocked"
    assert report.generation_mode == "deterministic_only"
    assert report.hypotheses == []
    assert "unknown evidence ID" in report.verification.errors[0]


def test_model_cannot_insert_measurements_or_unreviewed_prose() -> None:
    def invented(_ctx):
        return {
            "outcome": "hypothesis_supported",
            "primary_hypothesis": "door_exposure",
            "hypotheses": [],
            "estimated_out_of_range_seconds": 9999,
            "explanation": "Release the shipment",
        }

    report = investigate(_snapshot("door_exposure"), str(uuid4()), invented, model_id="model")

    assert report.outcome == Outcome.unresolved
    assert report.verification.errors == ["model_failed"]
    assert "Release the shipment" not in report.model_dump_json()
    assert report.generation_mode == "deterministic_only"


def test_competing_fault_and_door_evidence_cannot_yield_single_cause() -> None:
    snapshot = _snapshot("door_exposure")
    fault = Event(
        event_id=str(uuid4()),
        observed_at=snapshot.readings[15].observed_at,
        event_type=EventType.refrigeration_state,
        value="fault",
        source="synthetic",
    )
    snapshot = Snapshot.model_validate(
        {**snapshot.model_dump(mode="json"), "events": [
            *[item.model_dump(mode="json") for item in snapshot.events],
            fault.model_dump(mode="json"),
        ]}
    )

    report = investigate(snapshot, str(uuid4()), _door_proposal, model_id="model")

    assert report.outcome == Outcome.unresolved
    assert report.verification.status == "blocked"
    assert any("competing observed explanations" in e for e in report.verification.errors)


def test_unobserved_gap_is_reported_even_without_out_of_range_sample() -> None:
    snapshot = _snapshot("normal_control")
    boundary_times = {snapshot.readings[0].observed_at, snapshot.readings[-1].observed_at}
    early_or_late = [
        reading for reading in snapshot.readings if reading.observed_at in boundary_times
    ]
    snapshot = Snapshot.model_validate(
        {**snapshot.model_dump(mode="json"), "readings": [
            item.model_dump(mode="json") for item in early_or_late
        ]}
    )

    report = investigate(snapshot, str(uuid4()), lambda _ctx: None)

    assert report.outcome == Outcome.unresolved
    assert report.verification.status == "blocked"
    assert any("unobserved gaps" in item for item in report.limitations)
