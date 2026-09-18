from __future__ import annotations

import hashlib
from datetime import date

import pytest

from coldchain.contracts.enums import (
    Outcome,
    PublicStage,
    ReviewDecision,
    RunStatus,
    StageEventStatus,
)
from coldchain.contracts.schemas import ArtifactRef, Report, Snapshot, StageEvent
from coldchain.simulator import generate_snapshot
from coldchain.storage import (
    ActiveRunLimitExceededError,
    AwsStorage,
    ConditionalCheckFailedError,
    DailyLimitExceededError,
    NotFoundError,
    StateConflictError,
    TemporaryStorageError,
)

from .conftest import (
    NOW,
    FakeAwsError,
    FakeS3,
    RecordingDynamo,
    run_metadata,
    stable_uuid,
)


def _storage(dynamo=None, s3=None) -> AwsStorage:
    ids = iter(stable_uuid(f"aws-{i}") for i in range(20))
    return AwsStorage(
        bucket_name="private-artifacts",
        table_name="runs",
        s3_client=s3 or FakeS3(),
        dynamodb_client=dynamo or RecordingDynamo(),
        id_factory=lambda: next(ids),
        clock=lambda: NOW,
    )


def _run_item(
    run_id: str,
    snapshot: Snapshot,
    snapshot_ref: ArtifactRef,
    *,
    owner_sub: str = "operator",
    attempt_id: str = "attempt",
) -> dict:
    return {
        "PK": {"S": f"RUN#{run_id}"},
        "SK": {"S": "META"},
        "run_id": {"S": run_id},
        "owner_sub": {"S": owner_sub},
        "snapshot_id": {"S": snapshot.snapshot_id},
        "shipment_id": {"S": snapshot.shipment_id},
        "snapshot_key": {"S": snapshot_ref.key},
        "snapshot_sha256": {"S": snapshot_ref.sha256},
        "created_at": {"N": str(int(NOW.timestamp()))},
        "status": {"S": RunStatus.running.value},
        "stage": {"S": PublicStage.detecting.value},
        "stage_events": {"L": []},
        "attempt_id": {"S": attempt_id},
        "lease_expires_at": {"N": str(int(NOW.timestamp()) + 60)},
        "scenario_id": {"S": "normal_control"},
        "seed": {"N": "17"},
        "base_timestamp": {"S": NOW.isoformat()},
        "is_public_demo": {"BOOL": False},
    }


def _bound_report(
    report: Report,
    run_id: str,
    snapshot: Snapshot,
    snapshot_ref: ArtifactRef,
    *,
    report_id: str | None = None,
) -> Report:
    payload = report.model_dump(mode="python")
    payload.update(
        {
            "report_id": report_id or report.report_id,
            "run_id": run_id,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_sha256": snapshot_ref.sha256,
        }
    )
    for evidence in payload["evidence"]:
        evidence["snapshot_id"] = snapshot.snapshot_id
    return Report.model_validate(payload)


class ActiveLeaseDynamo(RecordingDynamo):
    """Small stateful fake for active-lease release and immediate reuse."""

    def __init__(self, active_run_id: str) -> None:
        super().__init__()
        self.active_run_id: str | None = active_run_id

    def transact_write_items(self, **kwargs):
        self.calls.append(("transact_write_items", kwargs))
        items = kwargs["TransactItems"]
        if len(items) == 4:
            if self.active_run_id is not None:
                reasons = [
                    {"Code": "None"},
                    {"Code": "None"},
                    {"Code": "ConditionalCheckFailed"},
                    {"Code": "None"},
                ]
                raise FakeAwsError("TransactionCanceledException", reasons)
            self.active_run_id = items[2]["Put"]["Item"]["run_id"]["S"]
        return {}

    def delete_item(self, **kwargs):
        self.calls.append(("delete_item", kwargs))
        expected = kwargs["ExpressionAttributeValues"][":run_id"]["S"]
        if self.active_run_id != expected:
            raise FakeAwsError("ConditionalCheckFailedException")
        self.active_run_id = None
        return {}


def test_create_transaction_persists_preparation_identity_and_all_limits() -> None:
    dynamo = RecordingDynamo()
    run = _storage(dynamo).create_or_get_run("operator", "secret-key", "hash", run_metadata())
    method, call = dynamo.calls[0]
    assert method == "transact_write_items"
    assert len(call["TransactItems"]) == 4
    serialized = str(call)
    assert "secret-key" not in serialized
    assert "scenario_id" in serialized
    assert "base_timestamp" in serialized
    assert "seed" in serialized
    assert run.preparation is not None


@pytest.mark.parametrize(
    ("index", "error_type"),
    [(1, DailyLimitExceededError), (2, ActiveRunLimitExceededError)],
)
def test_create_transaction_maps_limit_failures(index: int, error_type: type[Exception]) -> None:
    dynamo = RecordingDynamo()
    reasons = [{"Code": "None"} for _ in range(4)]
    reasons[index] = {"Code": "ConditionalCheckFailed"}
    dynamo.queue_error(
        "transact_write_items", FakeAwsError("TransactionCanceledException", reasons)
    )
    with pytest.raises(error_type):
        _storage(dynamo).create_or_get_run("operator", "key", "hash", run_metadata())


def test_idempotent_retry_wins_over_other_transaction_conflicts() -> None:
    dynamo = RecordingDynamo()
    reasons = [
        {"Code": "ConditionalCheckFailed"},
        {"Code": "None"},
        {"Code": "ConditionalCheckFailed"},
        {"Code": "ConditionalCheckFailed"},
    ]
    run_id = stable_uuid("existing")
    dynamo.queue_error(
        "transact_write_items", FakeAwsError("TransactionCanceledException", reasons)
    )
    dynamo.queue_response(
        "get_item",
        {"Item": {"request_hash": {"S": "hash"}, "run_id": {"S": run_id}}},
    )
    metadata = run_metadata(run_id=run_id)
    existing = _storage(dynamo)._decode_run(
        {
            "run_id": {"S": run_id},
            "owner_sub": {"S": "operator"},
            "snapshot_id": {"S": stable_uuid("existing-snapshot")},
            "shipment_id": {"S": stable_uuid("existing-shipment")},
            "created_at": {"N": str(int(NOW.timestamp()))},
            "status": {"S": "pending_enqueue"},
            "stage": {"S": "preparing"},
            "stage_events": {"L": []},
            "scenario_id": {"S": metadata["scenario_id"]},
            "seed": {"N": str(metadata["seed"])},
            "base_timestamp": {"S": metadata["base_timestamp"].isoformat()},
        }
    )
    dynamo.queue_response(
        "get_item",
        {
            "Item": {
                "run_id": {"S": existing.run_id},
                "owner_sub": {"S": "operator"},
                "snapshot_id": {"S": existing.snapshot_id},
                "shipment_id": {"S": existing.shipment_id},
                "created_at": {"N": str(int(existing.created_at.timestamp()))},
                "status": {"S": existing.status.value},
                "stage": {"S": existing.stage.value},
                "stage_events": {"L": []},
                "scenario_id": {"S": existing.scenario_id},
                "seed": {"N": str(existing.seed)},
                "base_timestamp": {"S": existing.base_timestamp.isoformat()},
            }
        },
    )
    assert _storage(dynamo).create_or_get_run("operator", "key", "hash", metadata).run_id == run_id


def test_canonical_artifacts_round_trip_and_are_immutable(snapshot, report) -> None:
    storage = _storage()
    snapshot_ref = storage.put_snapshot(snapshot)
    report_ref = storage.put_report(report)
    assert storage.get_snapshot(snapshot_ref) == snapshot
    assert storage.get_report(report_ref) == report
    with pytest.raises(StateConflictError):
        storage.put_report(report.model_copy(update={"outcome": Outcome.unresolved}))


def test_digest_not_found_and_provider_failures_use_canonical_errors(report) -> None:
    s3 = FakeS3()
    storage = _storage(s3=s3)
    reference = storage.put_report(report)
    s3.corrupt_reads[reference.key] = b"{}"
    with pytest.raises(StateConflictError):
        storage.get_report(reference)
    s3.corrupt_reads.clear()
    s3.objects.clear()
    with pytest.raises(NotFoundError):
        storage.get_report(reference)
    s3.fail_with = FakeAwsError("ServiceUnavailable")
    with pytest.raises(TemporaryStorageError, match="read artifact"):
        storage.get_report(reference)


@pytest.mark.parametrize("payload", [b"{", b"[]", b"{}"])
def test_schema_corrupt_s3_objects_are_explicit_conflicts(report, payload: bytes) -> None:
    s3 = FakeS3()
    storage = _storage(s3=s3)
    reference = storage.put_report(report)
    s3.corrupt_reads[reference.key] = payload
    matching_reference = ArtifactRef(
        key=reference.key,
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    with pytest.raises(StateConflictError, match="artifact"):
        storage.get_report(matching_reference)


def test_report_signer_uses_exact_key_and_bounded_expiry(report) -> None:
    s3 = FakeS3()
    storage = _storage(s3=s3)
    reference = storage.put_report(report)
    assert reference.key in storage.create_report_download_url(reference, 60)
    _, call = s3.calls[-1]
    assert call["Params"]["Key"] == reference.key
    assert call["ExpiresIn"] == 60
    with pytest.raises(ValueError):
        storage.create_report_download_url(reference, 301)
    with pytest.raises(ValueError):
        storage.create_report_download_url(
            ArtifactRef(
                key=reference.key.replace("reports/", "snapshots/"),
                sha256=reference.sha256,
            )
        )


def test_completion_rejects_another_runs_report(snapshot: Snapshot, report: Report) -> None:
    dynamo = RecordingDynamo()
    s3 = FakeS3()
    storage = _storage(dynamo=dynamo, s3=s3)
    snapshot_a_ref = storage.put_snapshot(snapshot)
    run_a_id = stable_uuid("aws-run-a")
    dynamo.queue_response(
        "get_item",
        {"Item": _run_item(run_a_id, snapshot, snapshot_a_ref)},
    )

    snapshot_b = Snapshot.model_validate(
        generate_snapshot(
            "normal_control",
            18,
            NOW,
            snapshot_id=stable_uuid("aws-snapshot-b"),
            shipment_id=stable_uuid("aws-shipment-b"),
        )
    )
    snapshot_b_ref = storage.put_snapshot(snapshot_b)
    report_b = _bound_report(
        report,
        stable_uuid("aws-run-b"),
        snapshot_b,
        snapshot_b_ref,
        report_id=stable_uuid("aws-report-b"),
    )
    report_b_ref = storage.put_report(report_b)

    with pytest.raises(
        ConditionalCheckFailedError,
        match="run_id, snapshot_id, snapshot_sha256",
    ):
        storage.complete_run(
            run_a_id,
            "attempt",
            RunStatus.completed,
            report_b_ref,
            "wrong report",
        )
    assert [method for method, _ in dynamo.calls] == ["get_item"]


def test_missing_s3_report_cannot_update_run_to_completed(snapshot: Snapshot) -> None:
    dynamo = RecordingDynamo()
    storage = _storage(dynamo=dynamo, s3=FakeS3())
    run_id = stable_uuid("aws-run-missing-report")
    snapshot_ref = ArtifactRef(
        key=f"snapshots/{snapshot.snapshot_id}.json",
        sha256="1" * 64,
    )
    dynamo.queue_response(
        "get_item",
        {"Item": _run_item(run_id, snapshot, snapshot_ref)},
    )
    missing_ref = ArtifactRef(
        key=f"reports/{stable_uuid('aws-missing-report')}.json",
        sha256="0" * 64,
    )

    with pytest.raises(NotFoundError, match="artifact"):
        storage.complete_run(
            run_id, "attempt", RunStatus.completed, missing_ref, "must not complete"
        )
    assert [method for method, _ in dynamo.calls] == ["get_item"]


def test_completion_releases_matching_lease_and_allows_immediate_next_run(
    snapshot: Snapshot, report: Report
) -> None:
    run_id = stable_uuid("aws-completed-run")
    dynamo = ActiveLeaseDynamo(run_id)
    s3 = FakeS3()
    storage = _storage(dynamo=dynamo, s3=s3)
    snapshot_ref = storage.put_snapshot(snapshot)
    bound_report = _bound_report(report, run_id, snapshot, snapshot_ref)
    report_ref = storage.put_report(bound_report)
    dynamo.queue_response(
        "get_item",
        {"Item": _run_item(run_id, snapshot, snapshot_ref)},
    )

    storage.complete_run(
        run_id,
        "attempt",
        RunStatus.completed,
        report_ref,
        "done",
    )
    assert dynamo.active_run_id is None
    delete_call = next(call for method, call in dynamo.calls if method == "delete_item")
    assert delete_call["ConditionExpression"] == "run_id = :run_id"

    next_run = storage.create_or_get_run("operator", "next-request", "next-hash", run_metadata())
    assert dynamo.active_run_id == next_run.run_id
    assert next_run.run_id != run_id


def test_completion_does_not_delete_a_newer_runs_active_lease(
    snapshot: Snapshot, report: Report
) -> None:
    completed_run_id = stable_uuid("aws-old-run")
    newer_run_id = stable_uuid("aws-new-run")
    dynamo = ActiveLeaseDynamo(newer_run_id)
    storage = _storage(dynamo=dynamo, s3=FakeS3())
    snapshot_ref = storage.put_snapshot(snapshot)
    bound_report = _bound_report(report, completed_run_id, snapshot, snapshot_ref)
    report_ref = storage.put_report(bound_report)
    dynamo.queue_response(
        "get_item",
        {"Item": _run_item(completed_run_id, snapshot, snapshot_ref)},
    )

    storage.complete_run(
        completed_run_id,
        "attempt",
        RunStatus.completed,
        report_ref,
        "done",
    )
    assert dynamo.active_run_id == newer_run_id


def test_queue_claim_stage_and_review_conditions_are_recorded() -> None:
    dynamo = RecordingDynamo()
    storage = _storage(dynamo=dynamo)
    run_id = stable_uuid("run")
    storage.mark_queued(run_id)
    assert "attribute_exists(snapshot_key)" in dynamo.calls[-1][1]["ConditionExpression"]
    assert storage.claim_run(run_id, "attempt", NOW, 90).success
    claim_call = dynamo.calls[-1][1]
    assert "lease_expires_at <= :now" in claim_call["ConditionExpression"]
    storage.set_stage(run_id, "attempt", PublicStage.detecting)
    event = StageEvent(
        event_id=stable_uuid("event"),
        stage=PublicStage.detecting,
        started_at=NOW,
        status=StageEventStatus.started,
    )
    storage.append_stage_event(run_id, "attempt", event)
    storage.save_review(
        run_id,
        "actor",
        stable_uuid("report"),
        ReviewDecision.acknowledged,
        "checked",
    )
    review_items = dynamo.calls[-1][1]["TransactItems"]
    assert "review_json" in str(review_items[0])
    assert "REVIEW#" in str(review_items[1])


def test_daily_and_active_helpers_use_conditional_writes() -> None:
    dynamo = RecordingDynamo()
    dynamo.queue_response("update_item", {"Attributes": {"count": {"N": "1"}}})
    storage = _storage(dynamo=dynamo)
    assert storage.claim_daily_run(date(2026, 9, 17), 50) == 1
    storage.claim_active_run("operator", stable_uuid("active"), NOW, 60)
    assert dynamo.calls[0][0] == "update_item"
    assert dynamo.calls[1][0] == "put_item"
    assert "expires_at <= :now" in dynamo.calls[1][1]["ConditionExpression"]
