"""Fake model for offline testing of the investigation agent.

Returns canned hypothesis proposals based on simple keyword matching
of tool results. This allows testing the full agent pipeline without
Bedrock access.
"""

from __future__ import annotations

from coldchain.contracts.enums import Assessment, HypothesisType, Outcome


def fake_investigate(tool_results: dict) -> dict:
    """Produce a model-like hypothesis proposal from tool results.

    Returns a dict matching the shape expected by the agent pipeline:
    outcome, primary_hypothesis, hypotheses list with evidence IDs.
    """
    hypotheses = []
    primary = None
    outcome = Outcome.unresolved

    door_events = tool_results.get("door_events", {}).get("events", [])
    refrig_events = tool_results.get("refrigeration_events", {}).get("events", [])
    comparisons = tool_results.get("sensor_comparison", {}).get("comparisons", [])
    excursion = tool_results.get("excursion_summary", {}).get("sensors", [])

    has_excursion = any(s.get("excursion_detected") for s in excursion)
    if not has_excursion:
        return {
            "outcome": Outcome.no_excursion,
            "primary_hypothesis": None,
            "hypotheses": [],
            "next_checks": [],
            "limitations": ["Simulated data", "Fake model used for offline testing"],
        }

    # Collect evidence IDs
    door_evidence = [e["evidence_id"] for e in door_events]
    refrig_evidence = [e["evidence_id"] for e in refrig_events]
    comparison_evidence = [c["evidence_id"] for c in comparisons]
    excursion_evidence = [s["evidence_id"] for s in excursion if s.get("excursion_detected")]

    # Simple rule: if door open events exist → door_exposure
    has_door_open = any(e.get("value") == "open" for e in door_events)
    has_refrig_fault = any(
        e.get("value") in ("stopped", "fault") for e in refrig_events
    )
    has_disagreement = any(
        c.get("max_difference_c", 0) > 2.0 for c in comparisons
    )

    # Door hypothesis
    if has_door_open:
        hypotheses.append({
            "hypothesis": HypothesisType.door_exposure,
            "assessment": Assessment.supported,
            "supporting_evidence_ids": door_evidence + excursion_evidence,
            "conflicting_evidence_ids": refrig_evidence if has_refrig_fault else [],
            "missing_evidence": [],
            "explanation": (
                "Door was opened during the excursion period. "
                "Temperature rise correlates with door opening."
            ),
        })
        if primary is None:
            primary = HypothesisType.door_exposure
            outcome = Outcome.hypothesis_supported
    else:
        hypotheses.append({
            "hypothesis": HypothesisType.door_exposure,
            "assessment": Assessment.insufficient,
            "supporting_evidence_ids": [],
            "conflicting_evidence_ids": [],
            "missing_evidence": ["No door opening events recorded"],
            "explanation": "No door state changes observed.",
        })

    # Refrigeration hypothesis
    if has_refrig_fault:
        hypotheses.append({
            "hypothesis": HypothesisType.refrigeration_problem,
            "assessment": Assessment.supported,
            "supporting_evidence_ids": refrig_evidence + excursion_evidence,
            "conflicting_evidence_ids": door_evidence if has_door_open else [],
            "missing_evidence": [],
            "explanation": (
                "Refrigeration system reported stopped/fault status "
                "correlating with temperature rise."
            ),
        })
        if primary is None:
            primary = HypothesisType.refrigeration_problem
            outcome = Outcome.hypothesis_supported
    else:
        hypotheses.append({
            "hypothesis": HypothesisType.refrigeration_problem,
            "assessment": Assessment.insufficient,
            "supporting_evidence_ids": [],
            "conflicting_evidence_ids": [],
            "missing_evidence": ["No refrigeration fault/stopped events"],
            "explanation": "No refrigeration problems observed.",
        })

    # Sensor disagreement hypothesis
    if has_disagreement:
        hypotheses.append({
            "hypothesis": HypothesisType.sensor_disagreement,
            "assessment": Assessment.supported,
            "supporting_evidence_ids": comparison_evidence,
            "conflicting_evidence_ids": [],
            "missing_evidence": [],
            "explanation": (
                "Significant temperature difference between reference and "
                "comparison sensors detected."
            ),
        })
        if primary is None:
            primary = HypothesisType.sensor_disagreement
            outcome = Outcome.hypothesis_supported
    else:
        hypotheses.append({
            "hypothesis": HypothesisType.sensor_disagreement,
            "assessment": Assessment.insufficient,
            "supporting_evidence_ids": [],
            "conflicting_evidence_ids": [],
            "missing_evidence": [],
            "explanation": "Sensors show consistent readings.",
        })

    # If multiple supported, mark unresolved
    supported_count = sum(
        1 for h in hypotheses if h["assessment"] == Assessment.supported
    )
    if supported_count > 1:
        outcome = Outcome.unresolved
        primary = None

    return {
        "outcome": outcome,
        "primary_hypothesis": primary,
        "hypotheses": hypotheses,
        "next_checks": [],
        "limitations": ["Simulated data", "Fake model used for offline testing"],
    }
