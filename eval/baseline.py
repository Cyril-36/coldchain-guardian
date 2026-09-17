"""Simple rules baseline for evaluation.

Provides the same hypothesis logic as the fake model but framed as
an evaluation baseline for comparison with the Strands agent.
Does not use any AI model — pure deterministic rules.

Baseline rules per VERIFICATION.md:
- Door temporal evidence → door hypothesis
- Explicit stopped/fault evidence with rising temps → refrigeration
- Sensor disagreement → check sensor
- Conflicts or missing evidence → unresolved
"""

from __future__ import annotations

from coldchain.contracts.enums import (
    Assessment,
    HypothesisType,
    Outcome,
)
from coldchain.investigation.tools import (
    ToolContext,
    get_door_events,
    get_excursion_summary,
    get_handling_policy,
    get_refrigeration_events,
    get_sensor_comparison,
    get_vehicle_events,
)


def baseline_investigate(ctx: ToolContext) -> dict:
    """Run the deterministic rules baseline on collected evidence."""
    excursion = get_excursion_summary(ctx)
    door = get_door_events(ctx)
    refrig = get_refrigeration_events(ctx)
    comparison = get_sensor_comparison(ctx)

    sensors = excursion.get("sensors", [])
    has_excursion = any(s.get("excursion_detected") for s in sensors)

    if not has_excursion:
        return {
            "outcome": Outcome.no_excursion,
            "primary_hypothesis": None,
            "hypotheses": [],
            "limitations": ["Rules baseline", "Simulated data"],
        }

    door_events = door.get("events", [])
    refrig_events = refrig.get("events", [])
    comparisons = comparison.get("comparisons", [])

    has_door_open = any(e.get("value") == "open" for e in door_events)
    has_refrig_fault = any(
        e.get("value") in ("stopped", "fault") for e in refrig_events
    )
    has_disagreement = any(
        c.get("max_difference_c", 0) > 2.0 for c in comparisons
    )

    door_evidence = [e["evidence_id"] for e in door_events]
    refrig_evidence = [e["evidence_id"] for e in refrig_events]
    comparison_evidence = [c["evidence_id"] for c in comparisons]
    excursion_evidence = [s["evidence_id"] for s in sensors if s.get("excursion_detected")]

    hypotheses = []
    supported_count = 0

    # Rule 1: Door temporal evidence
    if has_door_open:
        hypotheses.append({
            "hypothesis": HypothesisType.door_exposure,
            "assessment": Assessment.supported,
            "supporting_evidence_ids": door_evidence + excursion_evidence,
            "conflicting_evidence_ids": [],
            "missing_evidence": [],
            "explanation": "Door opening observed before/during temperature rise.",
        })
        supported_count += 1
    else:
        hypotheses.append({
            "hypothesis": HypothesisType.door_exposure,
            "assessment": Assessment.insufficient,
            "supporting_evidence_ids": [],
            "conflicting_evidence_ids": [],
            "missing_evidence": ["No door opening events"],
            "explanation": "No door state changes recorded.",
        })

    # Rule 2: Refrigeration fault/stopped
    if has_refrig_fault:
        hypotheses.append({
            "hypothesis": HypothesisType.refrigeration_problem,
            "assessment": Assessment.supported,
            "supporting_evidence_ids": refrig_evidence + excursion_evidence,
            "conflicting_evidence_ids": [],
            "missing_evidence": [],
            "explanation": "Refrigeration fault/stopped observed with rising temperatures.",
        })
        supported_count += 1
    else:
        hypotheses.append({
            "hypothesis": HypothesisType.refrigeration_problem,
            "assessment": Assessment.insufficient,
            "supporting_evidence_ids": [],
            "conflicting_evidence_ids": [],
            "missing_evidence": ["No refrigeration fault/stopped events"],
            "explanation": "No refrigeration problems recorded.",
        })

    # Rule 3: Sensor disagreement
    if has_disagreement:
        hypotheses.append({
            "hypothesis": HypothesisType.sensor_disagreement,
            "assessment": Assessment.supported,
            "supporting_evidence_ids": comparison_evidence,
            "conflicting_evidence_ids": [],
            "missing_evidence": [],
            "explanation": "Sensors show significant temperature disagreement.",
        })
        supported_count += 1
    else:
        hypotheses.append({
            "hypothesis": HypothesisType.sensor_disagreement,
            "assessment": Assessment.insufficient,
            "supporting_evidence_ids": [],
            "conflicting_evidence_ids": [],
            "missing_evidence": [],
            "explanation": "Sensors show consistent readings.",
        })

    # Determine outcome
    if supported_count == 0:
        outcome = Outcome.unresolved
        primary = None
    elif supported_count > 1:
        # Conflicts → unresolved
        outcome = Outcome.unresolved
        primary = None
    else:
        outcome = Outcome.hypothesis_supported
        primary = next(
            h["hypothesis"]
            for h in hypotheses
            if h["assessment"] == Assessment.supported
        )

    return {
        "outcome": outcome,
        "primary_hypothesis": primary,
        "hypotheses": hypotheses,
        "limitations": ["Rules baseline", "Simulated data"],
    }
