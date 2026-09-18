"""The rule baseline may cite only observations returned by the evidence tools."""

from datetime import UTC, datetime
from uuid import uuid4

from coldchain.contracts import Snapshot
from coldchain.contracts.enums import EventType, Outcome
from coldchain.contracts.schemas import Event
from coldchain.investigation.tools import (
    MAX_RETURNED_EVENTS,
    ToolContext,
    event_evidence_id,
    get_refrigeration_events,
)
from coldchain.simulator import generate_snapshot
from eval.baseline import baseline_proposal


def test_hidden_fault_is_not_cited_as_if_its_value_were_visible() -> None:
    snapshot = Snapshot.model_validate(
        generate_snapshot("door_exposure", 17, datetime(2026, 9, 18, tzinfo=UTC))
    )
    fault = Event(
        event_id=str(uuid4()),
        observed_at=snapshot.readings[-1].observed_at,
        event_type=EventType.refrigeration_state,
        value="fault",
        source="synthetic",
    )
    padding = [
        Event(
            event_id=str(uuid4()),
            observed_at=snapshot.readings[0].observed_at,
            event_type=EventType.refrigeration_state,
            value="running",
            source="synthetic",
        )
        for _ in range(MAX_RETURNED_EVENTS)
    ]
    padded = snapshot.model_copy(update={"events": [*snapshot.events, *padding, fault]})
    ctx = ToolContext(padded)
    visible = get_refrigeration_events(ctx)
    assert visible["has_fault_or_stopped"] is True
    assert visible["is_truncated"] is True
    assert all(event["value"] != "fault" for event in visible["events"])

    proposal = baseline_proposal(ctx)

    assert proposal.outcome == Outcome.unresolved
    hidden_fault_id = event_evidence_id(ctx, fault.event_id)
    assert all(
        hidden_fault_id != evidence_id
        for hypothesis in proposal.hypotheses
        for evidence_id in hypothesis.supporting_evidence_ids
    )
