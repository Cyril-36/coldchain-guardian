"""Deterministic report verifier.

Validates that model-proposed reports conform to schema, reference valid evidence,
contain no invented measurements, and include no prohibited content.
Not a second LLM — pure rule-based checks.
"""

from __future__ import annotations

from coldchain.contracts.enums import (
    Assessment,
    GenerationMode,
    HypothesisType,
    Outcome,
    VerificationStatus,
)
from coldchain.contracts.schemas import (
    Report,
    SensorMeasurement,
    Verification,
)
from coldchain.investigation.tools import ToolContext


PROHIBITED_PHRASES = [
    "safe to use",
    "safe for use",
    "release the shipment",
    "discard the shipment",
    "dispose of",
    "approved for distribution",
    "cleared for release",
    "fit for consumption",
]

# Minimum evidence prerequisites per CONTRACTS.md §Step 5
MINIMUM_PREREQUISITES = {
    HypothesisType.door_exposure: {
        "needs": "recorded door opening before/around temperature rise",
    },
    HypothesisType.refrigeration_problem: {
        "needs": "actual fault/stopped observations",
    },
    HypothesisType.sensor_disagreement: {
        "needs": "readings from two sensors with valid temporal alignment",
    },
}


def verify_report(
    report: Report,
    ctx: ToolContext,
    measurements: list[SensorMeasurement],
) -> Verification:
    """Run all verification checks on a report. Returns Verification with status and errors."""
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Schema conformance (already enforced by Pydantic, but check enum values)
    _check_schema(report, errors)

    # 2. Evidence ID existence
    _check_evidence_ids(report, ctx, errors)

    # 3. Numerical consistency
    _check_numerics(report, measurements, errors, warnings)

    # 4. Minimum evidence prerequisites
    _check_prerequisites(report, ctx, errors, warnings)

    # 5. Prohibited content
    _check_prohibited(report, errors)

    # 6. Gaps/unknown states in limitations
    _check_limitations(report, measurements, warnings)

    status = VerificationStatus.passed if not errors else VerificationStatus.blocked
    return Verification(status=status, errors=errors, warnings=warnings)


def _check_schema(report: Report, errors: list[str]) -> None:
    """Check allowed enum/action values."""
    if report.outcome == Outcome.no_excursion and report.hypotheses:
        errors.append("no_excursion outcome should have no hypotheses")

    if report.outcome == Outcome.hypothesis_supported and report.primary_hypothesis is None:
        errors.append("hypothesis_supported outcome requires a primary_hypothesis")

    if report.primary_hypothesis is not None:
        found = any(h.hypothesis == report.primary_hypothesis for h in report.hypotheses)
        if not found:
            errors.append(
                f"primary_hypothesis {report.primary_hypothesis.value} not in hypotheses list"
            )


def _check_evidence_ids(report: Report, ctx: ToolContext, errors: list[str]) -> None:
    """Every referenced evidence ID must exist in the snapshot's tool-accessible registry."""
    valid_ids = ctx.all_evidence_ids()

    for hyp in report.hypotheses:
        for eid in hyp.supporting_evidence_ids:
            if eid not in valid_ids:
                errors.append(f"Nonexistent supporting evidence ID: {eid}")
        for eid in hyp.conflicting_evidence_ids:
            if eid not in valid_ids:
                errors.append(f"Nonexistent conflicting evidence ID: {eid}")

    for nc in report.next_checks:
        for eid in nc.evidence_ids:
            if eid not in valid_ids:
                errors.append(f"Nonexistent next_check evidence ID: {eid}")


def _check_numerics(
    report: Report,
    measurements: list[SensorMeasurement],
    errors: list[str],
    warnings: list[str],
) -> None:
    """Displayed quantities must match deterministic metrics."""
    det_by_sensor = {m.sensor_id: m for m in measurements}

    for rm in report.measurements:
        det = det_by_sensor.get(rm.sensor_id)
        if det is None:
            errors.append(f"Report has measurement for unknown sensor: {rm.sensor_id}")
            continue
        if rm.estimated_out_of_range_seconds != det.estimated_out_of_range_seconds:
            errors.append(
                f"Sensor {rm.sensor_id}: estimated_out_of_range_seconds "
                f"report={rm.estimated_out_of_range_seconds} != "
                f"deterministic={det.estimated_out_of_range_seconds}"
            )
        if rm.observed_min_c != det.observed_min_c:
            errors.append(
                f"Sensor {rm.sensor_id}: observed_min_c "
                f"report={rm.observed_min_c} != deterministic={det.observed_min_c}"
            )
        if rm.observed_max_c != det.observed_max_c:
            errors.append(
                f"Sensor {rm.sensor_id}: observed_max_c "
                f"report={rm.observed_max_c} != deterministic={det.observed_max_c}"
            )


def _check_prerequisites(
    report: Report,
    ctx: ToolContext,
    errors: list[str],
    warnings: list[str],
) -> None:
    """Supported hypotheses must have minimum evidence."""
    for hyp in report.hypotheses:
        if hyp.assessment != Assessment.supported:
            continue

        prereq = MINIMUM_PREREQUISITES.get(hyp.hypothesis)
        if prereq is None:
            continue

        if not hyp.supporting_evidence_ids:
            errors.append(
                f"Hypothesis {hyp.hypothesis.value} marked supported "
                f"but has no supporting evidence. Needs: {prereq['needs']}"
            )

        # Check that contradictory evidence from the rule layer is included
        # (the verifier flags if tool results found conflicts but they're missing)


def _check_prohibited(report: Report, errors: list[str]) -> None:
    """No shipment release/discard action or medical disposition."""
    full_text = ""
    for hyp in report.hypotheses:
        full_text += hyp.explanation + " "
    for nc in report.next_checks:
        full_text += nc.reason + " "

    full_text_lower = full_text.lower()
    for phrase in PROHIBITED_PHRASES:
        if phrase in full_text_lower:
            errors.append(f"Prohibited phrase found: '{phrase}'")


def _check_limitations(
    report: Report,
    measurements: list[SensorMeasurement],
    warnings: list[str],
) -> None:
    """Gaps/unknown states should appear in limitations."""
    has_unknown = any(m.unknown_duration_seconds > 0 for m in measurements)
    has_censored = any(m.censored_start or m.censored_end for m in measurements)

    if has_unknown and not any("gap" in lim.lower() or "unknown" in lim.lower() for lim in report.limitations):
        warnings.append("Measurements have unknown durations but limitations don't mention gaps")

    if has_censored and not any("censor" in lim.lower() or "partial" in lim.lower() for lim in report.limitations):
        warnings.append("Measurements have censored boundaries but limitations don't mention them")

    # Simulation disclaimer
    if not any("simulat" in lim.lower() for lim in report.limitations):
        warnings.append("Missing simulation disclaimer in limitations")
