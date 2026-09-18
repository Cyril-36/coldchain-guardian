"""Deterministic checks on an investigator's typed evidence selection."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from coldchain.contracts.enums import (
    Assessment,
    DoorState,
    EventType,
    HypothesisType,
    Outcome,
    RefrigerationState,
    SensorRole,
)
from coldchain.investigation.tools import (
    ToolContext,
    event_evidence_id,
    get_door_events,
    get_excursion_summary,
    get_refrigeration_events,
    get_sensor_comparison,
)


class ProposedHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis: HypothesisType
    assessment: Assessment
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    conflicting_evidence_ids: list[str] = Field(default_factory=list)


class InvestigationProposal(BaseModel):
    """The model selects findings and IDs; it cannot write report prose or numbers."""

    model_config = ConfigDict(extra="forbid")

    outcome: Outcome
    primary_hypothesis: HypothesisType | None = None
    hypotheses: list[ProposedHypothesis] = Field(default_factory=list)


def verify_proposal(proposal: InvestigationProposal, ctx: ToolContext) -> list[str]:
    """Reject unsupported claims even when the proposal satisfies its JSON schema."""
    errors: list[str] = []
    snapshot = ctx.snapshot
    summary = get_excursion_summary(ctx)
    door = get_door_events(ctx)
    refrigeration = get_refrigeration_events(ctx)
    comparison = get_sensor_comparison(ctx)
    known = ctx.all_evidence_ids()

    reference_ids = {
        sensor["evidence_id"]
        for sensor in summary["sensors"]
        if sensor["role"] == SensorRole.reference and sensor["excursion_detected"]
    }
    excursion_ids = {
        sensor["evidence_id"] for sensor in summary["sensors"] if sensor["excursion_detected"]
    }
    # Derived from every snapshot event, not from the page the tools return to the
    # model. A fault beyond that page is still recorded evidence, and a finding that
    # omits it must be rejected.
    open_ids = {
        event_evidence_id(ctx, event.event_id)
        for event in snapshot.events
        if event.event_type == EventType.door_state and event.value == DoorState.open
    }
    fault_ids = {
        event_evidence_id(ctx, event.event_id)
        for event in snapshot.events
        if event.event_type == EventType.refrigeration_state
        and event.value in {RefrigerationState.fault, RefrigerationState.stopped}
    }
    comparison_ids = {
        item["evidence_id"]
        for item in comparison["comparisons"]
        if item["disagreement_detected"] and item["aligned_count"] > 0
    }
    temporal = door.get("temporal_facts") or {}
    temporal_id = temporal.get("evidence_id")
    door_supported = bool(
        open_ids
        and reference_ids
        and temporal_id
        and temporal.get("door_opened_before_rise")
        and temporal.get("overlap_measured_seconds", 0) > 0
    )
    refrigeration_supported = bool(fault_ids and reference_ids)
    # The tool caps returned events. A later fault still makes a single-cause
    # claim unsafe even when its evidence ID was outside that visible page.
    refrigeration_possible = bool(refrigeration["has_fault_or_stopped"] and reference_ids)
    disagreement_supported = bool(comparison_ids and excursion_ids)

    if proposal.outcome == Outcome.no_excursion:
        errors.append("an observed excursion cannot be reported as no_excursion")
    if len({item.hypothesis for item in proposal.hypotheses}) != len(proposal.hypotheses):
        errors.append("duplicate hypothesis")

    supported = [item for item in proposal.hypotheses if item.assessment == Assessment.supported]
    if proposal.outcome == Outcome.hypothesis_supported:
        if len(supported) != 1 or proposal.primary_hypothesis != supported[0].hypothesis:
            errors.append("primary hypothesis must be the single supported hypothesis")
    else:
        if proposal.primary_hypothesis is not None:
            errors.append("unresolved proposal cannot have a primary hypothesis")
        if supported:
            errors.append("unresolved proposal cannot contain a supported hypothesis")

    if (
        sum((door_supported, refrigeration_possible, disagreement_supported)) > 1
        and proposal.outcome == Outcome.hypothesis_supported
    ):
        errors.append("competing observed explanations require an unresolved outcome")

    for item in proposal.hypotheses:
        cited = set(item.supporting_evidence_ids)
        conflicting = set(item.conflicting_evidence_ids)
        if not cited | conflicting <= known:
            errors.append(f"{item.hypothesis}: unknown evidence ID")
            continue
        if cited & conflicting:
            errors.append(f"{item.hypothesis}: evidence cannot both support and conflict")
        if item.assessment != Assessment.supported:
            continue
        if item.hypothesis == HypothesisType.door_exposure:
            if not door_supported or not (
                cited & open_ids and cited & reference_ids and temporal_id in cited
            ):
                errors.append(
                    "door support lacks an observed opening, overlap, or reference excursion"
                )
        elif item.hypothesis == HypothesisType.refrigeration_problem:
            if not refrigeration_supported or not (cited & fault_ids and cited & reference_ids):
                errors.append("refrigeration support lacks fault/stopped and reference evidence")
        elif item.hypothesis == HypothesisType.sensor_disagreement and (
            not disagreement_supported or not (cited & comparison_ids and cited & excursion_ids)
        ):
            errors.append("sensor disagreement lacks aligned comparison and excursion evidence")

        if item.hypothesis == HypothesisType.door_exposure and fault_ids - conflicting:
            errors.append("door finding omits conflicting refrigeration fault evidence")
        if (
            item.hypothesis == HypothesisType.refrigeration_problem
            and door_supported
            and temporal_id not in conflicting
        ):
            errors.append("refrigeration finding omits conflicting door temporal evidence")

    for evidence_id in known:
        ref = ctx.get_evidence(evidence_id)
        if ref is None or ref.snapshot_id != snapshot.snapshot_id:
            errors.append("evidence belongs to another snapshot")
        elif (ref.observed_at and ref.observed_at > snapshot.cutoff_at) or (
            ref.interval and ref.interval.end_at > snapshot.cutoff_at
        ):
            errors.append("evidence exceeds snapshot cutoff")
    return errors
