"""The rule baseline the investigator is measured against.

docs/VERIFICATION.md section 3 fixes these rules:

    door temporal evidence            -> door hypothesis
    explicit stopped/fault evidence
      with rising temperatures        -> refrigeration hypothesis
    sensor disagreement               -> check sensor
    conflicts or missing evidence     -> unresolved

The baseline sees exactly the same observations as the model: it reads the same
read-only tools against the same ToolContext, and gets no labels. If it scores as well
as the investigator, that is a result to report, not to hide.
"""

from __future__ import annotations

from coldchain.contracts.enums import Assessment, HypothesisType, Outcome
from coldchain.investigation.tools import (
    ToolContext,
    event_evidence_id,
    get_door_events,
    get_excursion_summary,
    get_refrigeration_events,
    get_sensor_comparison,
)
from coldchain.investigation.verifier import InvestigationProposal, ProposedHypothesis


def baseline_proposal(ctx: ToolContext) -> InvestigationProposal:
    """Select hypotheses and evidence by rule alone, with no model involved."""
    summary = get_excursion_summary(ctx)
    door = get_door_events(ctx)
    comparison = get_sensor_comparison(ctx)
    # Called for its side effect: it registers evidence for every refrigeration event,
    # so the IDs cited below exist in the registry the verifier checks against. The
    # returned page is capped, so fault IDs are derived from the snapshot instead.
    get_refrigeration_events(ctx)

    reference_ids = [
        sensor["evidence_id"]
        for sensor in summary["sensors"]
        if sensor["role"] == "reference" and sensor["excursion_detected"]
    ]
    excursion_ids = [
        sensor["evidence_id"] for sensor in summary["sensors"] if sensor["excursion_detected"]
    ]
    open_ids = [
        event_evidence_id(ctx, event.event_id)
        for event in ctx.snapshot.events
        if event.event_type == "door_state" and event.value == "open"
    ]
    fault_ids = [
        event_evidence_id(ctx, event.event_id)
        for event in ctx.snapshot.events
        if event.event_type == "refrigeration_state" and event.value in {"fault", "stopped"}
    ]
    disagreement_ids = [
        item["evidence_id"]
        for item in comparison["comparisons"]
        if item["disagreement_detected"] and item["aligned_count"] > 0
    ]

    temporal = door.get("temporal_facts") or {}
    temporal_id = temporal.get("evidence_id")

    # Door: an observed opening before the rise with measured overlap.
    door_rule = bool(
        open_ids
        and reference_ids
        and temporal_id
        and temporal.get("door_opened_before_rise")
        and temporal.get("overlap_measured_seconds", 0) > 0
    )
    # Refrigeration: an explicit fault or stop alongside a reference excursion.
    refrigeration_rule = bool(fault_ids and reference_ids)
    # Sensor: aligned readings that actually disagree.
    sensor_rule = bool(disagreement_ids and excursion_ids)

    hypotheses: list[ProposedHypothesis] = []
    matched = [
        (HypothesisType.door_exposure, door_rule),
        (HypothesisType.refrigeration_problem, refrigeration_rule),
        (HypothesisType.sensor_disagreement, sensor_rule),
    ]
    winners = [kind for kind, hit in matched if hit]

    # More than one rule firing means the observations cannot separate explanations.
    single = winners[0] if len(winners) == 1 else None

    for kind, hit in matched:
        if kind == HypothesisType.door_exposure:
            supporting = [*reference_ids, *open_ids]
            if temporal_id:
                supporting.append(temporal_id)
            conflicting = list(fault_ids)
        elif kind == HypothesisType.refrigeration_problem:
            supporting = [*reference_ids, *fault_ids]
            conflicting = [temporal_id] if (temporal_id and door_rule) else []
        else:
            supporting = [*excursion_ids, *disagreement_ids]
            conflicting = []

        if hit and kind == single:
            assessment = Assessment.supported
        elif hit:
            # The rule fired but something else fired too: report it as unsettled
            # rather than as a second confident answer.
            assessment = Assessment.insufficient
        else:
            assessment = Assessment.insufficient
            supporting = []
            conflicting = []

        hypotheses.append(
            ProposedHypothesis(
                hypothesis=kind,
                assessment=assessment,
                supporting_evidence_ids=sorted(set(supporting)),
                conflicting_evidence_ids=sorted(set(conflicting)),
            )
        )

    if single is None:
        return InvestigationProposal(outcome=Outcome.unresolved, hypotheses=hypotheses)
    return InvestigationProposal(
        outcome=Outcome.hypothesis_supported,
        primary_hypothesis=single,
        hypotheses=hypotheses,
    )
