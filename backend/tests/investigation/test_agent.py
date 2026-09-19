"""Report behavior at the boundary between model choices and measured evidence."""

from datetime import UTC, datetime
from uuid import uuid4

from coldchain.contracts import Snapshot
from coldchain.contracts.enums import (
    Assessment,
    EventType,
    HypothesisType,
    Outcome,
    VerificationStatus,
)
from coldchain.contracts.schemas import Event
from coldchain.investigation.agent import investigate
from coldchain.investigation.tools import (
    MAX_RETURNED_EVENTS,
    ToolContext,
    get_door_events,
    get_excursion_summary,
    get_refrigeration_events,
)
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


def test_unresolved_proposal_cannot_embed_a_supported_hypothesis() -> None:
    def contradictory(ctx):
        proposal = _door_proposal(ctx)
        proposal.outcome = Outcome.unresolved
        proposal.primary_hypothesis = None
        return proposal

    report = investigate(_snapshot("door_exposure"), str(uuid4()), contradictory, model_id="model")

    assert report.outcome == Outcome.unresolved
    assert report.verification.status == "blocked"
    assert report.hypotheses == []
    assert any("unresolved" in error for error in report.verification.errors)


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


def test_door_finding_must_cite_a_fault_hidden_beyond_the_returned_event_page():
    """A conflicting fault outside the tool's visible page still blocks a door finding.

    The event tools cap the list returned to the model at MAX_RETURNED_EVENTS while
    registering evidence for every event. A verifier that reads fault evidence off
    that capped page sees no fault at all, so the contradiction check passes
    vacuously and a door explanation can be asserted while a recorded refrigeration
    fault goes uncited. AGENTS.md rule 5 requires contradictory evidence to survive
    into the report.
    """
    snapshot = _snapshot("door_exposure")
    events = list(snapshot.events)
    non_refrigeration = [e for e in events if e.event_type != EventType.refrigeration_state]
    refrigeration = [e for e in events if e.event_type == EventType.refrigeration_state]

    # Pad with running events so the single fault falls outside the returned page.
    padding = [
        Event(
            event_id=str(uuid4()),
            observed_at=snapshot.readings[0].observed_at,
            event_type=EventType.refrigeration_state,
            value="running",
            source="telemetry",
        )
        for _ in range(MAX_RETURNED_EVENTS)
    ]
    fault = Event(
        event_id=str(uuid4()),
        observed_at=snapshot.readings[-1].observed_at,
        event_type=EventType.refrigeration_state,
        value="fault",
        source="telemetry",
    )
    padded = snapshot.model_copy(
        update={"events": non_refrigeration + padding + refrigeration + [fault]}
    )

    ctx_probe = ToolContext(padded)
    refrigeration_result = get_refrigeration_events(ctx_probe)
    # Precondition: the fault is real, registered, and invisible on the returned page.
    assert refrigeration_result["has_fault_or_stopped"] is True
    assert refrigeration_result["is_truncated"] is True
    assert not [e for e in refrigeration_result["events"] if e["value"] == "fault"]

    # An unresolved outcome bypasses the competing-explanations check, so the
    # contradiction rule is the only thing standing between a "supported" door
    # hypothesis and a recorded fault that was never cited as conflicting.
    def proposal(ctx: ToolContext) -> InvestigationProposal:
        door = _door_proposal(ctx)
        return InvestigationProposal(
            outcome=Outcome.unresolved,
            primary_hypothesis=None,
            hypotheses=door.hypotheses,
        )

    report = investigate(padded, str(uuid4()), proposal)

    # The door explanation must not be asserted as supported with the fault uncited.
    assert report.verification.status == VerificationStatus.blocked
    assert not [h for h in report.hypotheses if h.assessment == Assessment.supported]
