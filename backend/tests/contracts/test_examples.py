"""Test suite validating all JSON contract example fixtures."""

from __future__ import annotations

import json
from pathlib import Path

from coldchain.contracts.enums import (
    GenerationMode,
    Outcome,
    PublicStage,
    RunStatus,
    VerificationStatus,
)
from coldchain.contracts.schemas import (
    Report,
    Run,
    Snapshot,
    snapshot_sha256,
)

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "contracts" / "examples"


def _load_json(filename: str) -> dict:
    path = EXAMPLES_DIR / filename
    assert path.is_file(), f"Example fixture missing: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_door_snapshot_example() -> None:
    data = _load_json("door-snapshot.json")
    snapshot = Snapshot.model_validate(data)

    assert snapshot.schema_version == "1.0"
    assert snapshot.source == "simulated"
    assert len(snapshot.sensors) == 2
    assert sum(1 for s in snapshot.sensors if s.role.value == "reference") == 1
    assert len(snapshot.readings) == 92
    assert len(snapshot.events) == 4

    digest = snapshot_sha256(snapshot)
    assert len(digest) == 64


def test_queued_run_example() -> None:
    data = _load_json("queued-run.json")
    run = Run.model_validate(data)

    assert run.status == RunStatus.queued
    assert run.stage == PublicStage.preparing
    assert run.snapshot_id is not None
    assert run.report_id is None
    assert run.is_public_demo is False


def test_running_run_example() -> None:
    data = _load_json("running-run.json")
    run = Run.model_validate(data)

    assert run.status == RunStatus.running
    assert run.stage == PublicStage.collecting_evidence
    assert run.snapshot_id is not None
    assert run.report_id is None
    assert len(run.stage_events) == 3
    assert run.generation_mode == GenerationMode.bedrock


def test_supported_report_example() -> None:
    data = _load_json("supported-report.json")
    report = Report.model_validate(data)

    assert report.outcome == Outcome.hypothesis_supported
    assert report.primary_hypothesis is not None
    assert report.primary_hypothesis.value == "door_exposure"
    assert report.verification.status == VerificationStatus.passed
    assert report.generation_mode == GenerationMode.bedrock
    assert report.review_required is True
    assert len(report.measurements) == 2
    assert len(report.hypotheses) == 3
    assert len(report.next_checks) == 2


def test_unresolved_report_example() -> None:
    data = _load_json("unresolved-report.json")
    report = Report.model_validate(data)

    assert report.outcome == Outcome.unresolved
    assert report.primary_hypothesis is None
    assert report.verification.status == VerificationStatus.passed
    assert report.generation_mode == GenerationMode.bedrock
    assert report.review_required is True
    assert all(h.assessment.value == "insufficient" for h in report.hypotheses)


def test_model_unavailable_report_example() -> None:
    data = _load_json("model-unavailable-report.json")
    report = Report.model_validate(data)

    assert report.outcome == Outcome.unresolved
    assert report.primary_hypothesis is None
    assert report.generation_mode == GenerationMode.deterministic_only
    assert report.verification.status == VerificationStatus.passed
    assert any("AI model unavailable" in lim for lim in report.limitations)


def test_normal_report_example() -> None:
    data = _load_json("normal-report.json")
    report = Report.model_validate(data)

    assert report.outcome == Outcome.no_excursion
    assert report.primary_hypothesis is None
    assert report.generation_mode == GenerationMode.deterministic_only
    assert report.review_required is False
    assert len(report.hypotheses) == 0


def test_failed_run_example() -> None:
    data = _load_json("failed-run.json")
    run = Run.model_validate(data)

    assert run.status == RunStatus.failed
    assert run.stage == PublicStage.failed
    assert run.error is not None
    assert run.report_id is None


def test_example_cross_reference_integrity() -> None:
    """Verify that report and run examples referencing door-snapshot.json
    have matching IDs and SHA-256.
    """
    snapshot_data = _load_json("door-snapshot.json")
    snapshot = Snapshot.model_validate(snapshot_data)
    expected_sha = snapshot_sha256(snapshot)

    queued_run = Run.model_validate(_load_json("queued-run.json"))
    assert queued_run.snapshot_id == snapshot.snapshot_id

    running_run = Run.model_validate(_load_json("running-run.json"))
    assert running_run.snapshot_id == snapshot.snapshot_id

    supported_report = Report.model_validate(_load_json("supported-report.json"))
    assert supported_report.snapshot_id == snapshot.snapshot_id
    assert supported_report.snapshot_sha256 == expected_sha
    assert len(supported_report.evidence) > 0

    unresolved_report = Report.model_validate(_load_json("unresolved-report.json"))
    assert unresolved_report.snapshot_id == snapshot.snapshot_id
    assert unresolved_report.snapshot_sha256 == expected_sha
    assert len(unresolved_report.evidence) > 0
