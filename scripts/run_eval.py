#!/usr/bin/env python3
"""Run the frozen holdout set and compute metrics from the saved outputs.

docs/VERIFICATION.md section 3. Two rules shape this script:

  * The investigator and the rule baseline see identical observations. Neither ever
    receives a case's family, note or expected answer.
  * Scores are computed by reading the written outputs back, so there is no path by
    which a number in the report was typed by hand.

Offline by default, using the rule baseline and zero model calls. `--proposer bedrock`
runs the real investigator and is the only mode that spends anything.
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from coldchain.contracts.enums import NextCheckCode, Outcome  # noqa: E402
from coldchain.contracts.schemas import Report, snapshot_sha256  # noqa: E402
from coldchain.investigation.agent import PROMPT_VERSION, investigate  # noqa: E402
from eval.baseline import baseline_proposal  # noqa: E402
from eval.holdout import HOLDOUT  # noqa: E402

DATASET_VERSION = "holdout-1.0.0"
CLEAR_FAMILIES = {"door", "refrigeration", "sensor"}
# Wording that would amount to a disposition decision about the product itself.
PROHIBITED_PHRASES = (
    "safe to use",
    "unsafe to use",
    "release the shipment",
    "discard the",
    "destroy the",
    "fit for use",
    "approved for use",
)


class _CountingProposer:
    """Wraps a proposer so invocations and failures are counted, not guessed.

    `uses_model` keeps the rule baseline from being reported as model traffic: the
    baseline is invoked exactly as often but costs nothing, and conflating the two
    would overstate what the run spent.
    """

    def __init__(self, inner: Any, *, uses_model: bool) -> None:
        self.inner = inner
        self.uses_model = uses_model
        self.calls = 0
        self.failures = 0

    def __call__(self, ctx: Any) -> Any:
        self.calls += 1
        try:
            return self.inner(ctx)
        except Exception:
            self.failures += 1
            raise


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return "unknown"


def _packages() -> dict[str, str]:
    versions = {}
    for name in ("strands-agents", "boto3", "botocore", "pydantic"):
        try:
            versions[name] = md.version(name)
        except md.PackageNotFoundError:
            versions[name] = "not-installed"
    return versions


def run_case(case: Any, proposer: Any, model_id: str | None) -> dict[str, Any]:
    """Investigate one case and return the record written to disk."""
    snapshot = case.snapshot()
    counting = (
        _CountingProposer(proposer, uses_model=model_id is not None) if proposer else None
    )
    started = time.monotonic()
    report = investigate(snapshot, str(uuid4()), counting, model_id=model_id)
    latency = round(time.monotonic() - started, 3)
    return {
        "case_id": case.case_id,
        "family": case.family,
        "expected_outcome": case.expected_outcome,
        "expected_hypothesis": case.expected_hypothesis,
        "snapshot_sha256": snapshot_sha256(snapshot),
        "latency_seconds": latency,
        "proposer_invocations": counting.calls if counting else 0,
        "model_invocations": (counting.calls if counting and counting.uses_model else 0),
        "structured_output_failures": counting.failures if counting else 0,
        "report": report.model_dump(mode="json"),
    }


def score(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute every metric by reading records back. Nothing here is hand-entered."""
    clear = [r for r in records if r["family"] in CLEAR_FAMILIES]
    ambiguous = [r for r in records if r["family"] == "ambiguous"]
    normal = [r for r in records if r["family"] == "normal"]

    def agrees(record: dict[str, Any]) -> bool:
        report = record["report"]
        return (
            report["outcome"] == record["expected_outcome"]
            and report["primary_hypothesis"] == record["expected_hypothesis"]
        )

    citation_errors: list[str] = []
    invented_evidence: list[str] = []
    numeric_errors: list[str] = []
    disposition_errors: list[str] = []

    for record in records:
        report = Report.model_validate(record["report"])
        available = {ref.evidence_id for ref in report.evidence}
        cited: set[str] = set()
        for hypothesis in report.hypotheses:
            cited |= set(hypothesis.supporting_evidence_ids)
            cited |= set(hypothesis.conflicting_evidence_ids)
        for check in report.next_checks:
            cited |= set(check.related_evidence_ids)

        missing = cited - available
        if missing:
            citation_errors.append(record["case_id"])
            invented_evidence.append(record["case_id"])

        # Every evidence item must belong to this snapshot and stay inside the cutoff.
        for ref in report.evidence:
            if ref.snapshot_id != report.snapshot_id:
                citation_errors.append(record["case_id"])
            if ref.interval and ref.interval.end_at > report.cutoff_at:
                citation_errors.append(record["case_id"])

        # The report's numbers must be the detector's, not the model's.
        if report.snapshot_sha256 != record["snapshot_sha256"]:
            numeric_errors.append(record["case_id"])

        text = " ".join(
            [*report.limitations, *(h.explanation or "" for h in report.hypotheses)]
        ).lower()
        if any(phrase in text for phrase in PROHIBITED_PHRASES):
            disposition_errors.append(record["case_id"])
        allowed = {code.value for code in NextCheckCode}
        if any(check.code not in allowed for check in report.next_checks):
            disposition_errors.append(record["case_id"])

    clear_hits = sum(agrees(r) for r in clear)
    ambiguous_unresolved = sum(
        r["report"]["outcome"] == Outcome.unresolved.value for r in ambiguous
    )
    normal_hits = sum(r["report"]["outcome"] == Outcome.no_excursion.value for r in normal)
    normal_model_calls = sum(r["model_invocations"] for r in normal)

    return {
        "clear_category_agreement": {
            "numerator": clear_hits,
            "denominator": len(clear),
            "misses": [r["case_id"] for r in clear if not agrees(r)],
        },
        "ambiguous_unresolved": {
            "numerator": ambiguous_unresolved,
            "denominator": len(ambiguous),
            "misses": [
                r["case_id"] for r in ambiguous
                if r["report"]["outcome"] != Outcome.unresolved.value
            ],
        },
        "normal_correct": {"numerator": normal_hits, "denominator": len(normal)},
        "normal_model_invocations": normal_model_calls,
        "citation_validity_errors": sorted(set(citation_errors)),
        "invented_evidence": sorted(set(invented_evidence)),
        "unsupported_numeric_claims": sorted(set(numeric_errors)),
        "prohibited_disposition_actions": sorted(set(disposition_errors)),
        "total_model_invocations": sum(r["model_invocations"] for r in records),
        "total_proposer_invocations": sum(r["proposer_invocations"] for r in records),
        "structured_output_failures": sum(r["structured_output_failures"] for r in records),
        "latency_seconds": {
            "total": round(sum(r["latency_seconds"] for r in records), 3),
            "max": round(max((r["latency_seconds"] for r in records), default=0.0), 3),
        },
        "outcome_distribution": dict(
            Counter(r["report"]["outcome"] for r in records)
        ),
    }


def stability(records: list[dict[str, Any]], repeats: list[dict[str, Any]]) -> dict[str, Any]:
    """Report every attempt for the repeated cases, not just whether they agreed."""
    grouped: dict[str, list[str]] = {}
    for record in [*records, *repeats]:
        if record["case_id"] in {"door_01", "ambiguous_01"}:
            grouped.setdefault(record["case_id"], []).append(record["report"]["outcome"])
    return {
        case_id: {"attempts": outcomes, "stable": len(set(outcomes)) == 1}
        for case_id, outcomes in grouped.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposer", choices=("baseline", "bedrock", "none"), default="baseline")
    parser.add_argument("--out", default=None, help="output directory (default: eval/reports/<ts>)")
    parser.add_argument("--region", default=None)
    parser.add_argument("--model-id", default=None)
    args = parser.parse_args()

    model_id = None
    if args.proposer == "baseline":
        proposer: Any = baseline_proposal
    elif args.proposer == "none":
        proposer = None
    else:
        if not args.region or not args.model_id:
            print("bedrock mode requires --region and --model-id", file=sys.stderr)
            return 2
        from coldchain.investigation.bedrock import BedrockProposer

        proposer = BedrockProposer(args.model_id, args.region)
        model_id = args.model_id

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) if args.out else REPO_ROOT / "eval" / "reports" / stamp
    outputs_dir = out_dir / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)

    records = [run_case(case, proposer, model_id) for case in HOLDOUT]
    for record in records:
        (outputs_dir / f"{record['case_id']}.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )

    # Stability: one clear and one ambiguous case, two further attempts each.
    repeats: list[dict[str, Any]] = []
    by_id = {case.case_id: case for case in HOLDOUT}
    for case_id in ("door_01", "ambiguous_01"):
        for attempt in (2, 3):
            record = run_case(by_id[case_id], proposer, model_id)
            repeats.append(record)
            (outputs_dir / f"{case_id}.attempt{attempt}.json").write_text(
                json.dumps(record, indent=2), encoding="utf-8"
            )

    # Scores are computed from what was written, not from what was in memory.
    reloaded = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(outputs_dir.glob("*.json"))
        if ".attempt" not in path.name
    ]
    reloaded_repeats = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(outputs_dir.glob("*.attempt*.json"))
    ]

    result = {
        "manifest": {
            "dataset_version": DATASET_VERSION,
            "case_count": len(HOLDOUT),
            "commit_sha": _git_sha(),
            "prompt_version": PROMPT_VERSION,
            "model_id": model_id,
            "proposer": args.proposer,
            "packages": _packages(),
            "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
        "metrics": score(reloaded),
        "stability": stability(reloaded, reloaded_repeats),
    }
    (out_dir / "metrics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"\nwrote {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
