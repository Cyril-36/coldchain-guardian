from __future__ import annotations

from datetime import timedelta
from typing import Any

from coldchain.contracts import (
    GenerationMode,
    Outcome,
    Report,
    RunStatus,
    Verification,
    VerificationStatus,
)
from coldchain.storage import MemoryStorage, TemporaryStorageError

from .conftest import NOW, FakeQueue, decode, event


def _create(app: Any, *, seed: int = 7, key: str = "idem-1") -> dict[str, Any]:
    return app.handle(
        event(
            "POST",
            "/v1/runs",
            body={"scenario_id": "door_exposure", "seed": seed},
            headers={"Idempotency-Key": key},
        )
    )


def test_create_run_persists_snapshot_before_enqueue_and_returns_202(api_factory) -> None:
    app, storage, queue = api_factory()

    response = _create(app)

    assert response["statusCode"] == 202
    body = decode(response)
    run = storage.get_run(body["run_id"])
    assert run is not None
    assert run.status == RunStatus.queued
    assert run.snapshot_ref is not None
    assert queue.messages == [(run.run_id, run.snapshot_id, "1.0")]
    snapshot = storage.get_snapshot(run.snapshot_ref)
    assert snapshot.snapshot_id == run.snapshot_id
    serialized = snapshot.model_dump_json()
    assert "door_exposure" not in serialized
    assert "scenario_id" not in serialized


def test_repeat_post_returns_same_run_without_second_enqueue(api_factory) -> None:
    app, _, queue = api_factory()

    first = _create(app)
    second = _create(app)

    assert decode(first)["run_id"] == decode(second)["run_id"]
    assert len(queue.messages) == 1


def test_same_key_with_different_body_returns_conflict(api_factory) -> None:
    app, _, _ = api_factory()
    _create(app, seed=1)

    response = _create(app, seed=2)

    assert response["statusCode"] == 409
    assert decode(response)["error"]["code"] == "idempotency_conflict"


def test_queue_failure_retains_pending_run_and_retry_resumes_it(api_factory) -> None:
    queue = FakeQueue()
    queue.failures_remaining = 1
    app, storage, _ = api_factory(queue=queue)

    failed = _create(app)

    assert failed["statusCode"] == 503
    assert decode(failed)["error"]["retryable"] is True
    run_id = failed["headers"]["x-run-id"]
    run = storage.get_run(run_id)
    assert run is not None and run.status == RunStatus.pending_enqueue
    assert run.snapshot_ref is not None

    retried = _create(app)
    assert retried["statusCode"] == 202
    assert decode(retried)["run_id"] == run_id
    assert len(queue.messages) == 1


class FailingStorage(MemoryStorage):
    def create_or_get_run(self, *args, **kwargs):
        raise TemporaryStorageError("database unavailable")


def test_storage_failure_before_reservation_does_not_enqueue(api_factory) -> None:
    queue = FakeQueue()
    app, _, _ = api_factory(storage=FailingStorage(clock=lambda: NOW), queue=queue)

    response = _create(app)

    assert response["statusCode"] == 503
    assert queue.messages == []


def test_snapshot_and_run_require_owner(api_factory) -> None:
    app, _, _ = api_factory()
    run_id = decode(_create(app))["run_id"]

    owned = app.handle(event("GET", f"/v1/runs/{run_id}/snapshot"))
    other = app.handle(event("GET", f"/v1/runs/{run_id}", subject="operator-b"))

    assert owned["statusCode"] == 200
    assert other["statusCode"] == 404


def test_report_and_download_are_conflicts_until_ready(api_factory) -> None:
    app, _, _ = api_factory()
    run_id = decode(_create(app))["run_id"]

    report = app.handle(event("GET", f"/v1/runs/{run_id}/report"))
    download = app.handle(event("GET", f"/v1/runs/{run_id}/download"))

    assert report["statusCode"] == 409
    assert download["statusCode"] == 409
    assert decode(report)["error"]["code"] == "report_not_ready"


def complete_run(storage: MemoryStorage, run_id: str) -> Report:
    run = storage.get_run(run_id)
    assert run is not None and run.snapshot_ref is not None
    snapshot = storage.get_snapshot(run.snapshot_ref)
    claim = storage.claim_run(run_id, "attempt-1", NOW, 60)
    assert claim.success
    report = Report(
        report_id="c6394d25-55a6-4f5e-a57d-99f7e0392832",
        run_id=run_id,
        snapshot_id=run.snapshot_id,
        snapshot_sha256=run.snapshot_ref.sha256,
        detector_version="test",
        created_at=NOW + timedelta(minutes=1),
        cutoff_at=snapshot.cutoff_at,
        measurements=[],
        outcome=Outcome.no_excursion,
        primary_hypothesis=None,
        hypotheses=[],
        next_checks=[],
        limitations=["Synthetic demonstration data."],
        verification=Verification(status=VerificationStatus.passed),
        generation_mode=GenerationMode.deterministic_only,
        review_required=False,
        evidence=[],
    )
    reference = storage.put_report(report)
    storage.complete_run(run_id, "attempt-1", RunStatus.completed, reference, "complete")
    return report


def test_review_actor_is_server_derived_and_stale_report_is_rejected(api_factory) -> None:
    app, storage, _ = api_factory()
    run_id = decode(_create(app))["run_id"]
    report = complete_run(storage, run_id)

    spoofed = app.handle(
        event(
            "POST",
            f"/v1/runs/{run_id}/review",
            body={
                "decision": "acknowledged",
                "note": "Reviewed evidence only.",
                "report_id": report.report_id,
                "actor_sub": "attacker",
            },
        )
    )
    assert spoofed["statusCode"] == 422

    stale = app.handle(
        event(
            "POST",
            f"/v1/runs/{run_id}/review",
            body={
                "decision": "acknowledged",
                "note": "",
                "report_id": "01af524f-505f-4cb8-9a7a-a4e2fe423a43",
            },
        )
    )
    assert stale["statusCode"] == 409

    accepted = app.handle(
        event(
            "POST",
            f"/v1/runs/{run_id}/review",
            body={
                "decision": "request_more_evidence",
                "note": "Check missing logs.",
                "report_id": report.report_id,
            },
        )
    )
    assert accepted["statusCode"] == 200
    assert decode(accepted)["actor_sub"] == "operator-a"


def test_download_uses_five_minute_signing_window(api_factory) -> None:
    app, storage, _ = api_factory()
    run_id = decode(_create(app))["run_id"]
    complete_run(storage, run_id)

    response = app.handle(event("GET", f"/v1/runs/{run_id}/download"))

    assert response["statusCode"] == 200
    assert decode(response)["url"].endswith("expires_in=300")
