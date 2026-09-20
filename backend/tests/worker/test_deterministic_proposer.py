"""The deployed Bedrock-disabled path must explain excursions, not just detect them.

Before this, the worker passed proposer=None whenever Bedrock was disabled, so
investigate() returned model_unavailable for every excursion: the deployed system
could detect an excursion but never propose a cause. These tests pin the wired
behaviour at the worker level, which is the path that actually runs in AWS.
"""

from datetime import UTC, datetime
from uuid import uuid4

from coldchain.contracts import QueueMessage, Snapshot
from coldchain.contracts.enums import RunStatus
from coldchain.simulator import generate_snapshot
from coldchain.storage import MemoryStorage
from coldchain.worker import handler as handler_module
from coldchain.worker.handler import process_message

NOW = datetime(2026, 9, 18, tzinfo=UTC)


def _queued(scenario: str, storage: MemoryStorage) -> QueueMessage:
    run = storage.create_or_get_run(
        "operator-a",
        str(uuid4()),
        str(uuid4()),
        {"scenario_id": scenario, "seed": 17, "base_timestamp": NOW},
    )
    snapshot = Snapshot.model_validate(
        generate_snapshot(
            scenario, 17, NOW, snapshot_id=run.snapshot_id, shipment_id=run.shipment_id
        )
    )
    storage.attach_snapshot(run.run_id, storage.put_snapshot(snapshot))
    storage.mark_queued(run.run_id)
    return QueueMessage(run_id=run.run_id, snapshot_id=run.snapshot_id)


def _report_for(scenario: str, storage: MemoryStorage):
    message = _queued(scenario, storage)
    # No proposer and no model_id: exactly how the Bedrock-disabled Lambda calls it.
    assert process_message(message, storage) == "completed"
    run = storage.get_run(message.run_id)
    assert run is not None and run.report_ref is not None
    return run, storage.get_report(run.report_ref)


def _supported(report) -> list[str]:
    return [h.hypothesis for h in report.hypotheses if h.assessment == "supported"]


def test_door_exposure_is_supported_in_deterministic_mode() -> None:
    storage = MemoryStorage()
    run, report = _report_for("door_exposure", storage)

    assert report.outcome == "hypothesis_supported"
    assert report.primary_hypothesis == "door_exposure"
    assert _supported(report) == ["door_exposure"]
    assert report.verification.status == "passed"
    assert report.verification.errors == []
    assert run.status == RunStatus.completed
    # A rule proposer is not a model: the report must not claim otherwise.
    assert report.generation_mode == "deterministic_only"
    assert report.model_id is None


def test_refrigeration_problem_is_supported_in_deterministic_mode() -> None:
    storage = MemoryStorage()
    run, report = _report_for("refrigeration_problem", storage)

    assert report.outcome == "hypothesis_supported"
    assert report.primary_hypothesis == "refrigeration_problem"
    assert _supported(report) == ["refrigeration_problem"]
    assert report.verification.status == "passed"
    assert run.status == RunStatus.completed
    assert report.generation_mode == "deterministic_only"
    assert report.model_id is None


def test_ambiguous_incident_stays_unresolved() -> None:
    """Honesty guard: the rule proposer must not manufacture a cause."""
    storage = MemoryStorage()
    run, report = _report_for("ambiguous_incident", storage)

    assert report.outcome == "unresolved"
    assert report.primary_hypothesis is None
    assert _supported(report) == []
    assert run.status == RunStatus.needs_review
    assert report.generation_mode == "deterministic_only"


def test_normal_control_never_calls_a_proposer() -> None:
    storage = MemoryStorage()
    calls: list[object] = []

    def spy(ctx):  # pragma: no cover - must never run
        calls.append(ctx)
        raise AssertionError("a normal run must not consult a proposer")

    message = _queued("normal_control", storage)
    assert process_message(message, storage, proposer=spy) == "completed"
    assert calls == []
    run = storage.get_run(message.run_id)
    assert run is not None and run.status == RunStatus.completed


def test_bedrock_disabled_worker_supplies_the_rule_proposer(monkeypatch) -> None:
    """Pins the wiring itself, not just its observable effect."""
    storage = MemoryStorage()
    seen: list[object] = []
    real = handler_module.rule_proposal

    def tracking(ctx):
        seen.append(ctx)
        return real(ctx)

    monkeypatch.setattr(handler_module, "rule_proposal", tracking)
    message = _queued("door_exposure", storage)
    # model_id omitted == Bedrock disabled in the deployed configuration.
    assert process_message(message, storage) == "completed"
    assert len(seen) == 1
