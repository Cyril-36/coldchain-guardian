from __future__ import annotations

from datetime import date

import pytest

from coldchain.contracts.enums import Outcome, PublicStage, ReviewDecision, StageEventStatus
from coldchain.contracts.schemas import ArtifactRef, StageEvent
from coldchain.storage import (
    ActiveRunLimitExceededError,
    AwsStorage,
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
    assert (
        _storage(dynamo).create_or_get_run("operator", "key", "hash", metadata).run_id
        == run_id
    )


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
