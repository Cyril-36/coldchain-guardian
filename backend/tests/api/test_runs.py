from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from coldchain.contracts import (
    GenerationMode,
    Outcome,
    Report,
    RunStatus,
    Verification,
    VerificationStatus,
)
from coldchain.simulator import generate_snapshot
from coldchain.storage import MemoryStorage, TemporaryStorageError

from .conftest import NOW, FakeQueue, decode, event


def _create(
    app: Any,
    *,
    seed: int = 7,
    key: str = "idem-1",
    subject: str | None = "operator-a",
) -> dict[str, Any]:
    return app.handle(
        event(
            "POST",
            "/v1/runs",
            body={"scenario_id": "door_exposure", "seed": seed},
            headers={"Idempotency-Key": key},
            subject=subject,
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


def test_repeat_after_completion_returns_same_terminal_run_without_enqueue(api_factory) -> None:
    app, storage, queue = api_factory()
    first = _create(app)
    run_id = decode(first)["run_id"]
    complete_run(storage, run_id)

    repeated = _create(app)

    assert repeated["statusCode"] == 202
    assert decode(repeated)["run_id"] == run_id
    assert decode(repeated)["status"] == "completed"
    assert len(queue.messages) == 1


def test_repeat_preserves_all_preparation_and_snapshot_identity(api_factory) -> None:
    generator_calls: list[tuple[Any, ...]] = []

    def recording_generator(*args, **kwargs):
        generator_calls.append((*args, kwargs))
        return generate_snapshot(*args, **kwargs)

    app, storage, _ = api_factory(snapshot_generator=recording_generator)
    first_id = decode(_create(app))["run_id"]
    first = storage.get_run(first_id)
    assert first is not None and first.snapshot_ref is not None
    first_snapshot = storage.get_snapshot(first.snapshot_ref).model_dump_json()

    second_id = decode(_create(app))["run_id"]
    second = storage.get_run(second_id)

    assert second is not None
    assert second_id == first_id
    assert second.snapshot_id == first.snapshot_id
    assert second.shipment_id == first.shipment_id
    assert second.seed == first.seed
    assert second.base_timestamp == first.base_timestamp
    assert second.snapshot_ref == first.snapshot_ref
    assert storage.get_snapshot(second.snapshot_ref).model_dump_json() == first_snapshot
    assert len(generator_calls) == 1


def test_same_key_is_scoped_independently_per_operator(api_factory) -> None:
    app, _, queue = api_factory()

    first = _create(app, subject="operator-a")
    second = _create(app, subject="operator-b")

    assert first["statusCode"] == second["statusCode"] == 202
    assert decode(first)["run_id"] != decode(second)["run_id"]
    assert len(queue.messages) == 2


def test_idempotent_retry_does_not_consume_another_daily_reservation(api_factory) -> None:
    storage = MemoryStorage(clock=lambda: NOW, daily_run_limit=2)
    app, _, _ = api_factory(
        storage=storage,
        allowed_operator_subs=frozenset({"operator-a", "operator-b", "operator-c"}),
    )

    assert _create(app, subject="operator-a")["statusCode"] == 202
    assert _create(app, subject="operator-a")["statusCode"] == 202
    assert _create(app, subject="operator-b")["statusCode"] == 202
    limited = _create(app, subject="operator-c")

    assert limited["statusCode"] == 429
    assert decode(limited)["error"]["code"] == "daily_limit_exceeded"


def test_same_key_with_different_body_returns_conflict(api_factory) -> None:
    app, _, _ = api_factory()
    _create(app, seed=1)

    response = _create(app, seed=2)

    assert response["statusCode"] == 409
    assert decode(response)["error"]["code"] == "idempotency_conflict"


def test_queue_failure_retains_pending_run_and_retry_resumes_it(api_factory) -> None:
    queue = FakeQueue()
    queue.failures_remaining = 1
    generator_calls = 0

    def generator(*args, **kwargs):
        nonlocal generator_calls
        generator_calls += 1
        return generate_snapshot(*args, **kwargs)

    app, storage, _ = api_factory(queue=queue, snapshot_generator=generator)

    failed = _create(app)

    assert failed["statusCode"] == 503
    assert decode(failed)["error"]["retryable"] is True
    run_id = failed["headers"]["x-run-id"]
    run = storage.get_run(run_id)
    assert run is not None and run.status == RunStatus.pending_enqueue
    assert run.snapshot_ref is not None
    original_ref = run.snapshot_ref

    retried = _create(app)
    assert retried["statusCode"] == 202
    assert decode(retried)["run_id"] == run_id
    assert len(queue.messages) == 1
    resumed = storage.get_run(run_id)
    assert resumed is not None and resumed.snapshot_ref == original_ref
    assert generator_calls == 1


def test_simulator_failure_after_reservation_is_retryable_on_same_run(api_factory) -> None:
    calls = 0

    def fail_once_generator(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("private simulator diagnostic")
        return generate_snapshot(*args, **kwargs)

    app, storage, queue = api_factory(snapshot_generator=fail_once_generator)

    failed = _create(app)

    assert failed["statusCode"] == 503
    assert decode(failed)["error"]["retryable"] is True
    assert "private simulator diagnostic" not in failed["body"]
    run_id = failed["headers"]["x-run-id"]
    pending = storage.get_run(run_id)
    assert pending is not None and pending.status == RunStatus.pending_enqueue
    assert pending.snapshot_ref is None
    assert queue.messages == []

    retried = _create(app)
    assert retried["statusCode"] == 202
    assert decode(retried)["run_id"] == run_id
    assert calls == 2
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
    assert decode(response)["error"]["message"] == "Storage is temporarily unavailable"


class OperationFailingStorage(MemoryStorage):
    fail_operation: str | None = None

    def _fail(self, operation: str) -> None:
        if self.fail_operation == operation:
            raise TemporaryStorageError("private provider failure")

    def put_snapshot(self, snapshot):
        self._fail("put_snapshot")
        return super().put_snapshot(snapshot)

    def attach_snapshot(self, run_id, snapshot_ref):
        self._fail("attach_snapshot")
        return super().attach_snapshot(run_id, snapshot_ref)

    def get_report(self, report_ref):
        self._fail("get_report")
        return super().get_report(report_ref)

    def create_report_download_url(self, report_ref, expires_in_seconds=300):
        self._fail("create_report_download_url")
        return super().create_report_download_url(report_ref, expires_in_seconds)

    def save_review(self, run_id, actor_sub, report_id, decision, note):
        self._fail("save_review")
        return super().save_review(run_id, actor_sub, report_id, decision, note)


@pytest.mark.parametrize("operation", ["put_snapshot", "attach_snapshot"])
def test_snapshot_storage_failure_never_enqueues(api_factory, operation: str) -> None:
    storage = OperationFailingStorage(clock=lambda: NOW)
    storage.fail_operation = operation
    queue = FakeQueue()
    app, _, _ = api_factory(storage=storage, queue=queue)

    response = _create(app)

    assert response["statusCode"] == 503
    assert decode(response)["error"]["retryable"] is True
    assert "private provider failure" not in response["body"]
    assert queue.messages == []
    run = storage.get_run(response["headers"]["x-run-id"])
    assert run is not None and run.status == RunStatus.pending_enqueue
    assert run.snapshot_ref is None


class FailOnceMarkQueuedStorage(MemoryStorage):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.mark_attempts = 0

    def mark_queued(self, run_id: str) -> None:
        self.mark_attempts += 1
        if self.mark_attempts == 1:
            raise TemporaryStorageError("provider response containing private internals")
        super().mark_queued(run_id)


def test_queue_success_then_state_update_failure_retries_same_run_and_snapshot(
    api_factory,
) -> None:
    storage = FailOnceMarkQueuedStorage(clock=lambda: NOW)
    queue = FakeQueue()
    app, _, _ = api_factory(storage=storage, queue=queue)

    failed = _create(app)
    run_id = failed["headers"]["x-run-id"]
    before = storage.get_run(run_id)
    assert failed["statusCode"] == 503
    assert before is not None and before.status == RunStatus.pending_enqueue
    assert before.snapshot_ref is not None

    retried = _create(app)
    after = storage.get_run(run_id)

    assert retried["statusCode"] == 202
    assert decode(retried)["run_id"] == run_id
    assert after is not None and after.status == RunStatus.queued
    assert after.snapshot_ref == before.snapshot_ref
    assert len(queue.messages) == 2


def test_worker_claim_between_send_and_mark_queued_is_not_regressed(api_factory) -> None:
    storage = MemoryStorage(clock=lambda: NOW)
    queue = FakeQueue()
    queue.on_send = lambda run_id, _snapshot_id: storage.claim_run(
        run_id, "worker-attempt", NOW, 60
    )
    app, _, _ = api_factory(storage=storage, queue=queue)

    response = _create(app)
    run = storage.get_run(decode(response)["run_id"])

    assert response["statusCode"] == 202
    assert run is not None and run.status == RunStatus.running
    assert decode(response)["status"] == "running"


def test_active_run_limit_maps_to_429(api_factory) -> None:
    app, _, _ = api_factory()
    assert _create(app, key="first")["statusCode"] == 202

    response = _create(app, key="second")

    assert response["statusCode"] == 429
    assert decode(response)["error"]["code"] == "active_run_limit_exceeded"


def test_snapshot_and_run_require_owner(api_factory) -> None:
    app, _, _ = api_factory()
    run_id = decode(_create(app))["run_id"]

    owned = app.handle(event("GET", f"/v1/runs/{run_id}/snapshot"))
    other = app.handle(event("GET", f"/v1/runs/{run_id}", subject="operator-b"))

    assert owned["statusCode"] == 200
    assert other["statusCode"] == 404


def test_owner_can_read_run_and_missing_run_is_404(api_factory) -> None:
    app, _, _ = api_factory()
    run_id = decode(_create(app))["run_id"]

    owned = app.handle(event("GET", f"/v1/runs/{run_id}"))
    missing = app.handle(event("GET", "/v1/runs/00000000-0000-4000-8000-000000000000"))

    assert owned["statusCode"] == 200
    assert decode(owned)["run_id"] == run_id
    assert missing["statusCode"] == 404


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


def test_both_review_decisions_are_append_only_and_note_limit_is_enforced(api_factory) -> None:
    app, storage, _ = api_factory()
    run_id = decode(_create(app))["run_id"]
    report = complete_run(storage, run_id)

    for decision in ("acknowledged", "request_more_evidence"):
        response = app.handle(
            event(
                "POST",
                f"/v1/runs/{run_id}/review",
                body={"decision": decision, "note": decision, "report_id": report.report_id},
            )
        )
        assert response["statusCode"] == 200
        assert decode(response)["decision"] == decision

    assert [review.decision.value for review in storage.list_reviews(run_id)] == [
        "acknowledged",
        "request_more_evidence",
    ]
    excessive = app.handle(
        event(
            "POST",
            f"/v1/runs/{run_id}/review",
            body={
                "decision": "acknowledged",
                "note": "x" * 1001,
                "report_id": report.report_id,
            },
        )
    )
    assert excessive["statusCode"] == 422


def test_review_accepts_exact_note_limit_and_rejects_unknown_decision(api_factory) -> None:
    app, storage, _ = api_factory()
    run_id = decode(_create(app))["run_id"]
    report = complete_run(storage, run_id)

    at_limit = app.handle(
        event(
            "POST",
            f"/v1/runs/{run_id}/review",
            body={
                "decision": "acknowledged",
                "note": "x" * 1000,
                "report_id": report.report_id,
            },
        )
    )
    unknown = app.handle(
        event(
            "POST",
            f"/v1/runs/{run_id}/review",
            body={
                "decision": "approved",
                "note": "must not create a safety state",
                "report_id": report.report_id,
            },
        )
    )

    assert at_limit["statusCode"] == 200
    assert len(decode(at_limit)["note"]) == 1000
    assert unknown["statusCode"] == 422


def test_non_owner_cannot_read_completed_resources_or_review(api_factory) -> None:
    app, storage, _ = api_factory()
    run_id = decode(_create(app))["run_id"]
    report = complete_run(storage, run_id)

    for suffix in ("", "/snapshot", "/report", "/download"):
        response = app.handle(event("GET", f"/v1/runs/{run_id}{suffix}", subject="operator-b"))
        assert response["statusCode"] == 404
    review = app.handle(
        event(
            "POST",
            f"/v1/runs/{run_id}/review",
            subject="operator-b",
            body={"decision": "acknowledged", "note": "", "report_id": report.report_id},
        )
    )
    assert review["statusCode"] == 404


def test_completed_report_is_available_to_owner(api_factory) -> None:
    app, storage, _ = api_factory()
    run_id = decode(_create(app))["run_id"]
    report = complete_run(storage, run_id)

    response = app.handle(event("GET", f"/v1/runs/{run_id}/report"))

    assert response["statusCode"] == 200
    assert decode(response)["report_id"] == report.report_id


@pytest.mark.parametrize(
    ("operation", "method", "suffix", "body"),
    [
        ("get_report", "GET", "/report", None),
        ("create_report_download_url", "GET", "/download", None),
        (
            "save_review",
            "POST",
            "/review",
            {"decision": "acknowledged", "note": "checked"},
        ),
    ],
)
def test_post_completion_storage_failures_are_sanitized(
    api_factory,
    operation: str,
    method: str,
    suffix: str,
    body: dict[str, str] | None,
) -> None:
    storage = OperationFailingStorage(clock=lambda: NOW)
    app, _, _ = api_factory(storage=storage)
    run_id = decode(_create(app))["run_id"]
    report = complete_run(storage, run_id)
    storage.fail_operation = operation
    request_body = None if body is None else {**body, "report_id": report.report_id}

    response = app.handle(event(method, f"/v1/runs/{run_id}{suffix}", body=request_body))

    assert response["statusCode"] == 503
    assert decode(response)["error"]["retryable"] is True
    assert "private provider failure" not in response["body"]


def test_download_uses_five_minute_signing_window(api_factory) -> None:
    app, storage, _ = api_factory()
    run_id = decode(_create(app))["run_id"]
    complete_run(storage, run_id)

    response = app.handle(event("GET", f"/v1/runs/{run_id}/download"))

    assert response["statusCode"] == 200
    assert decode(response)["url"].endswith("expires_in=300")


def test_download_ignores_client_supplied_object_path(api_factory) -> None:
    app, storage, _ = api_factory()
    run_id = decode(_create(app))["run_id"]
    complete_run(storage, run_id)
    request = event("GET", f"/v1/runs/{run_id}/download")
    request["body"] = '{"s3_key":"private/arbitrary-object.json"}'

    response = app.handle(request)

    assert response["statusCode"] == 200
    assert "arbitrary-object" not in decode(response)["url"]


class RecordingStorage(MemoryStorage):
    def __init__(self, calls: list[str]):
        super().__init__(clock=lambda: NOW)
        self.calls = calls

    def create_or_get_run(self, *args, **kwargs):
        self.calls.append("reserve_idempotency_and_limits")
        return super().create_or_get_run(*args, **kwargs)

    def put_snapshot(self, snapshot):
        self.calls.append("persist_snapshot")
        return super().put_snapshot(snapshot)

    def attach_snapshot(self, run_id, snapshot_ref):
        self.calls.append("attach_snapshot")
        return super().attach_snapshot(run_id, snapshot_ref)

    def mark_queued(self, run_id):
        self.calls.append("mark_queued")
        return super().mark_queued(run_id)


def test_run_creation_call_order_is_reserve_generate_persist_attach_enqueue(api_factory) -> None:
    calls: list[str] = []
    storage = RecordingStorage(calls)
    queue = FakeQueue()
    queue.on_send = lambda _run_id, _snapshot_id: calls.append("enqueue")

    def generator(*args, **kwargs):
        calls.append("generate_snapshot")
        return generate_snapshot(*args, **kwargs)

    app, _, _ = api_factory(
        storage=storage,
        queue=queue,
        snapshot_generator=generator,
    )

    assert _create(app)["statusCode"] == 202
    assert calls == [
        "reserve_idempotency_and_limits",
        "generate_snapshot",
        "persist_snapshot",
        "attach_snapshot",
        "enqueue",
        "mark_queued",
    ]


@pytest.mark.parametrize("seed", [True, False, "1", "not-an-integer", 1.5])
def test_invalid_seed_is_rejected(api_factory, seed) -> None:
    app, _, _ = api_factory()
    response = app.handle(
        event(
            "POST",
            "/v1/runs",
            body={"scenario_id": "normal_control", "seed": seed},
            headers={"Idempotency-Key": "key"},
        )
    )
    assert response["statusCode"] == 422
