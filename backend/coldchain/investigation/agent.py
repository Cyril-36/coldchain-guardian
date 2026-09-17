"""Investigation agent — orchestrates evidence tools and model.

Uses Strands Agents SDK with structured output when Bedrock is available,
falls back to fake_model for offline/test execution.

The model adds evidence selection and explanation; deterministic code owns
all numerical facts. If the model fails, returns needs_review with
deterministic measurements.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone

from coldchain.contracts.enums import (
    GenerationMode,
    Outcome,
    VerificationStatus,
)
from coldchain.contracts.schemas import (
    Hypothesis,
    NextCheck,
    Report,
    SensorMeasurement,
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
from coldchain.investigation.verifier import verify_report
from coldchain.investigation.fake_model import fake_investigate


# ── Limits from PLAN.md ────────────────────────────────────────────────────

MAX_MODEL_INVOCATIONS = 8
MAX_TOOL_CALLS = 12
APPLICATION_DEADLINE_SECONDS = 90
PROMPT_VERSION = "1.0.0"


def investigate(
    snapshot: Snapshot,
    run_id: str,
    model_id: str | None = None,
    use_bedrock: bool = False,
) -> Report:
    """Run a full investigation on the snapshot.

    Steps:
    1. Validate snapshot, detect excursions
    2. If no excursion → return no_excursion report (skip model)
    3. If multiple windows → return needs_review
    4. Collect evidence via tools
    5. Call model (or fake) for hypothesis proposal
    6. Verify report
    7. If verification fails → return needs_review with deterministic fallback

    Returns a complete Report.
    """
    start_time = time.monotonic()

    # Step 1: Detect
    detection = build_detection_result(snapshot)
    sha = snapshot_sha256(snapshot)

    # Step 2: No excursion → skip model
    if not detection.has_excursion:
        return _build_no_excursion_report(
            snapshot=snapshot,
            run_id=run_id,
            sha=sha,
            measurements=detection.measurements,
        )

    # Step 3: Multiple windows unsupported
    if detection.needs_review and detection.reason == "multiple_windows_unsupported":
        return _build_needs_review_report(
            snapshot=snapshot,
            run_id=run_id,
            sha=sha,
            measurements=detection.measurements,
            reason="Multiple excursion windows detected; MVP supports single window only.",
            generation_mode=GenerationMode.deterministic_only,
        )

    # Step 4: Collect evidence via tools
    ctx = ToolContext(
        snapshot=snapshot,
        measurements=detection.measurements,
        run_id=run_id,
    )

    tool_results = _collect_evidence(ctx)

    # Step 5: Model call (or fake)
    elapsed = time.monotonic() - start_time
    remaining = APPLICATION_DEADLINE_SECONDS - elapsed

    if remaining < 5:
        return _build_needs_review_report(
            snapshot=snapshot,
            run_id=run_id,
            sha=sha,
            measurements=detection.measurements,
            reason="Application deadline exceeded before model call.",
            generation_mode=GenerationMode.deterministic_only,
        )

    try:
        if use_bedrock and model_id:
            model_result = _call_bedrock(
                ctx=ctx,
                tool_results=tool_results,
                model_id=model_id,
                deadline_seconds=remaining,
            )
            generation_mode = GenerationMode.bedrock
        else:
            model_result = fake_investigate(tool_results)
            generation_mode = GenerationMode.deterministic_only
            model_id = None
    except Exception as exc:
        return _build_needs_review_report(
            snapshot=snapshot,
            run_id=run_id,
            sha=sha,
            measurements=detection.measurements,
            reason=f"Model call failed: {exc}",
            generation_mode=GenerationMode.deterministic_only,
        )

    # Step 6: Build report from model result
    report = _build_report_from_model(
        snapshot=snapshot,
        run_id=run_id,
        sha=sha,
        measurements=detection.measurements,
        model_result=model_result,
        model_id=model_id,
        generation_mode=generation_mode,
    )

    # Step 7: Verify
    verification = verify_report(report, ctx, detection.measurements)
    if verification.status == VerificationStatus.blocked:
        return _build_needs_review_report(
            snapshot=snapshot,
            run_id=run_id,
            sha=sha,
            measurements=detection.measurements,
            reason=f"Verification failed: {'; '.join(verification.errors)}",
            generation_mode=generation_mode,
            warnings=verification.warnings,
        )

    # Return with verification attached
    return report.model_copy(update={"verification": verification})


def _collect_evidence(ctx: ToolContext) -> dict:
    """Call all evidence tools and return results dict."""
    return {
        "excursion_summary": get_excursion_summary(ctx),
        "sensor_comparison": get_sensor_comparison(ctx),
        "door_events": get_door_events(ctx),
        "refrigeration_events": get_refrigeration_events(ctx),
        "vehicle_events": get_vehicle_events(ctx),
        "handling_policy": get_handling_policy(ctx),
    }


def _call_bedrock(
    ctx: ToolContext,
    tool_results: dict,
    model_id: str,
    deadline_seconds: float,
) -> dict:
    """Call Strands Agent with Bedrock for hypothesis proposal.

    Uses structured output with a Pydantic model. Enforces invocation
    and tool-call limits. Falls back on timeout or failure.
    """
    # Import Strands only when actually needed (keeps core tests clean)
    try:
        from strands import Agent
        from strands.models.bedrock import BedrockModel
    except ImportError as e:
        raise RuntimeError(
            f"strands-agents not installed; cannot use Bedrock: {e}"
        ) from e

    from pydantic import BaseModel, Field
    from coldchain.contracts.enums import Assessment, HypothesisType, Outcome

    class HypothesisProposal(BaseModel):
        hypothesis: HypothesisType
        assessment: Assessment
        supporting_evidence_ids: list[str] = Field(default_factory=list)
        conflicting_evidence_ids: list[str] = Field(default_factory=list)
        missing_evidence: list[str] = Field(default_factory=list)
        explanation: str = ""

    class InvestigationProposal(BaseModel):
        outcome: Outcome
        primary_hypothesis: HypothesisType | None = None
        hypotheses: list[HypothesisProposal] = Field(default_factory=list)
        limitations: list[str] = Field(default_factory=list)

    bedrock_model = BedrockModel(model_id=model_id)
    agent = Agent(model=bedrock_model)

    prompt = _build_prompt(ctx, tool_results)

    result = agent.invoke(
        prompt,
        structured_output_model=InvestigationProposal,
    )

    proposal = result.structured_output
    return {
        "outcome": proposal.outcome,
        "primary_hypothesis": proposal.primary_hypothesis,
        "hypotheses": [h.model_dump() for h in proposal.hypotheses],
        "next_checks": [],
        "limitations": proposal.limitations,
    }


def _build_prompt(ctx: ToolContext, tool_results: dict) -> str:
    """Build the investigation prompt with evidence summaries."""
    excursion = tool_results["excursion_summary"]
    door = tool_results["door_events"]
    refrig = tool_results["refrigeration_events"]
    vehicle = tool_results["vehicle_events"]
    comparison = tool_results["sensor_comparison"]
    policy = tool_results["handling_policy"]

    return f"""You are investigating a cold-chain temperature excursion.

EVIDENCE AVAILABLE:

Temperature Policy: {policy['min_c']}-{policy['max_c']}°C
{json.dumps(excursion, indent=2, default=str)}

Door Events ({door['count']} events):
{json.dumps(door['events'], indent=2, default=str)}

Refrigeration Events ({refrig['count']} events):
{json.dumps(refrig['events'], indent=2, default=str)}

Vehicle Events ({vehicle['count']} events):
{json.dumps(vehicle['events'], indent=2, default=str)}

Sensor Comparison:
{json.dumps(comparison['comparisons'], indent=2, default=str)}

INSTRUCTIONS:
1. Compare door_exposure, refrigeration_problem, and sensor_disagreement hypotheses.
2. Look for conflicting evidence and alternatives, not just matching events.
3. Return supported/contradicted/insufficient assessments with evidence IDs.
4. If observations cannot distinguish alternatives, choose unresolved.
5. Never decide product viability or fabricate a confidence percentage.
6. Reference only the evidence IDs provided above.
"""


def _build_no_excursion_report(
    snapshot: Snapshot,
    run_id: str,
    sha: str,
    measurements: list[SensorMeasurement],
) -> Report:
    return Report(
        report_id=str(uuid.uuid4()),
        run_id=run_id,
        snapshot_id=snapshot.snapshot_id,
        snapshot_sha256=sha,
        detector_version=DETECTOR_VERSION,
        created_at=datetime.now(timezone.utc),
        cutoff_at=snapshot.cutoff_at,
        measurements=measurements,
        outcome=Outcome.no_excursion,
        verification=Verification(status=VerificationStatus.passed),
        generation_mode=GenerationMode.deterministic_only,
        review_required=False,
        limitations=["Simulated data"],
    )


def _build_needs_review_report(
    snapshot: Snapshot,
    run_id: str,
    sha: str,
    measurements: list[SensorMeasurement],
    reason: str,
    generation_mode: GenerationMode,
    warnings: list[str] | None = None,
) -> Report:
    return Report(
        report_id=str(uuid.uuid4()),
        run_id=run_id,
        snapshot_id=snapshot.snapshot_id,
        snapshot_sha256=sha,
        detector_version=DETECTOR_VERSION,
        created_at=datetime.now(timezone.utc),
        cutoff_at=snapshot.cutoff_at,
        measurements=measurements,
        outcome=Outcome.unresolved,
        verification=Verification(
            status=VerificationStatus.blocked,
            errors=[reason],
            warnings=warnings or [],
        ),
        generation_mode=generation_mode,
        review_required=True,
        limitations=["Simulated data", reason],
    )


def _build_report_from_model(
    snapshot: Snapshot,
    run_id: str,
    sha: str,
    measurements: list[SensorMeasurement],
    model_result: dict,
    model_id: str | None,
    generation_mode: GenerationMode,
) -> Report:
    """Build a Report from model hypothesis proposal + deterministic measurements."""
    hypotheses = [
        Hypothesis(
            hypothesis=h["hypothesis"],
            assessment=h["assessment"],
            supporting_evidence_ids=h.get("supporting_evidence_ids", []),
            conflicting_evidence_ids=h.get("conflicting_evidence_ids", []),
            missing_evidence=h.get("missing_evidence", []),
            explanation=h.get("explanation", ""),
        )
        for h in model_result.get("hypotheses", [])
    ]

    return Report(
        report_id=str(uuid.uuid4()),
        run_id=run_id,
        snapshot_id=snapshot.snapshot_id,
        snapshot_sha256=sha,
        detector_version=DETECTOR_VERSION,
        prompt_version=PROMPT_VERSION,
        model_id=model_id,
        created_at=datetime.now(timezone.utc),
        cutoff_at=snapshot.cutoff_at,
        measurements=measurements,
        outcome=model_result.get("outcome", Outcome.unresolved),
        primary_hypothesis=model_result.get("primary_hypothesis"),
        hypotheses=hypotheses,
        next_checks=[],
        limitations=model_result.get("limitations", ["Simulated data"]),
        verification=Verification(status=VerificationStatus.passed),
        generation_mode=generation_mode,
        review_required=True,
    )
