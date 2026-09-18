"""Build a report from deterministic facts and a verified, typed proposal."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from coldchain.contracts.enums import (
    Assessment,
    GenerationMode,
    NextCheckCode,
    Outcome,
    VerificationStatus,
)
from coldchain.contracts.schemas import (
    Hypothesis,
    NextCheck,
    Report,
    Snapshot,
    Verification,
    snapshot_sha256,
)
from coldchain.core.detector import DETECTOR_VERSION, build_detection_result
from coldchain.investigation.tools import (
    ToolContext,
    get_door_events,
    get_excursion_summary,
    get_handling_policy,
    get_refrigeration_events,
    get_sensor_comparison,
    get_vehicle_events,
)
from coldchain.investigation.verifier import InvestigationProposal, verify_proposal
from coldchain.storage import StorageError

PROMPT_VERSION = "1.0.0"
Proposer = Callable[[ToolContext], InvestigationProposal]

_EXPLANATIONS = {
    "door_exposure": (
        "A recorded door opening overlaps the measured temperature excursion; "
        "causation is not proven."
    ),
    "refrigeration_problem": (
        "A refrigeration fault or stop was recorded during an observed excursion; "
        "causation is not proven."
    ),
    "sensor_disagreement": (
        "Aligned air sensors disagree; this does not identify which sensor is faulty."
    ),
}


def investigate(
    snapshot: Snapshot,
    run_id: str,
    proposer: Proposer | None = None,
    *,
    model_id: str | None = None,
) -> Report:
    """A normal run skips the model; every other model failure degrades visibly."""
    detection = build_detection_result(snapshot)
    ctx = ToolContext(snapshot, detection.measurements, run_id=run_id)
    get_excursion_summary(ctx)
    limitations = [
        "Synthetic air-sensor telemetry; this report does not decide product safety or release."
    ]
    if any(m.unknown_duration_seconds > 0 for m in detection.measurements):
        limitations.append("Temperature coverage contains unobserved gaps.")
    if any(m.censored_start or m.censored_end for m in detection.measurements):
        limitations.append("An excursion reaches the edge of observed coverage.")
    if any(m.sample_count == 0 for m in detection.measurements):
        limitations.append("At least one configured sensor has no readings.")

    if not detection.has_excursion and not detection.needs_review:
        return _report(
            ctx, run_id, Outcome.no_excursion, limitations, VerificationStatus.passed
        )
    if detection.reason:
        return _report(
            ctx,
            run_id,
            Outcome.unresolved,
            limitations + [f"Investigation requires review: {detection.reason}."],
            VerificationStatus.blocked,
            error=detection.reason,
        )

    # Populate a complete, snapshot-bound registry for verification. These calls
    # are deterministic; the model receives facts only through its read-only tools.
    for tool in (
        get_excursion_summary,
        get_sensor_comparison,
        get_door_events,
        get_refrigeration_events,
        get_vehicle_events,
        get_handling_policy,
    ):
        tool(ctx)
    if not proposer:
        return _report(
            ctx,
            run_id,
            Outcome.unresolved,
            limitations + [
                "AI investigation was unavailable; deterministic measurements remain visible."
            ],
            VerificationStatus.blocked,
            error="model_unavailable",
        )

    try:
        proposal = InvestigationProposal.model_validate(proposer(ctx))
    except StorageError:
        raise
    except Exception:
        return _report(
            ctx,
            run_id,
            Outcome.unresolved,
            limitations + ["AI investigation failed; deterministic measurements remain visible."],
            VerificationStatus.blocked,
            error="model_failed",
        )
    errors = verify_proposal(proposal, ctx)
    if errors:
        return _report(
            ctx,
            run_id,
            Outcome.unresolved,
            limitations + ["Proposed evidence did not pass deterministic verification."],
            VerificationStatus.blocked,
            error="proposal_rejected: " + "; ".join(errors),
        )

    hypotheses = [
        Hypothesis(
            hypothesis=item.hypothesis,
            assessment=item.assessment,
            supporting_evidence_ids=item.supporting_evidence_ids,
            conflicting_evidence_ids=item.conflicting_evidence_ids,
            missing_evidence=(
                ["More observations are required to establish this explanation."]
                if item.assessment == Assessment.insufficient
                else []
            ),
            explanation=(
                _EXPLANATIONS[item.hypothesis.value]
                if item.assessment == Assessment.supported
                else "Available evidence does not establish this explanation."
            ),
        )
        for item in proposal.hypotheses
    ]
    if proposal.outcome == Outcome.unresolved:
        limitations.append("Available evidence does not establish one explanation.")
    return _report(
        ctx,
        run_id,
        proposal.outcome,
        limitations,
        VerificationStatus.passed,
        hypotheses=hypotheses,
        primary_hypothesis=proposal.primary_hypothesis,
        model_id=model_id,
    )


def _report(
    ctx: ToolContext,
    run_id: str,
    outcome: Outcome,
    limitations: list[str],
    status: VerificationStatus,
    *,
    error: str | None = None,
    hypotheses: list[Hypothesis] | None = None,
    primary_hypothesis=None,
    model_id: str | None = None,
) -> Report:
    measurement_ids = [eid for m in ctx.measurements for eid in m.evidence_ids]
    if outcome == Outcome.no_excursion:
        next_checks: list[NextCheck] = []
    else:
        next_checks = [
            NextCheck(
                code=(
                    NextCheckCode.request_missing_logs
                    if outcome == Outcome.unresolved
                    else NextCheckCode.quality_review
                ),
                reason="A qualified operator should review the observations and missing evidence.",
                related_evidence_ids=measurement_ids,
            )
        ]
    return Report(
        report_id=str(uuid4()),
        run_id=run_id,
        snapshot_id=ctx.snapshot_id,
        snapshot_sha256=snapshot_sha256(ctx.snapshot),
        detector_version=DETECTOR_VERSION,
        prompt_version=PROMPT_VERSION,
        model_id=model_id,
        created_at=datetime.now(UTC),
        cutoff_at=ctx.cutoff_at,
        measurements=ctx.measurements,
        outcome=outcome,
        primary_hypothesis=primary_hypothesis,
        hypotheses=hypotheses or [],
        next_checks=next_checks,
        limitations=limitations,
        verification=Verification(status=status, errors=[error] if error else []),
        generation_mode=(GenerationMode.bedrock if model_id else GenerationMode.deterministic_only),
        review_required=outcome != Outcome.no_excursion,
        evidence=ctx.all_evidence(),
    )
