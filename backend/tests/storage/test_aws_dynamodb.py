from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from coldchain.storage import (
    ActiveRunConflict,
    ArtifactNotFound,
    AwsStorage,
    DailyLimitExceeded,
    ReviewConflict,
)

from .conftest import FakeAwsError, FakeS3, RecordingDynamo, stable_uuid

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def _storage(dynamo: RecordingDynamo, s3: FakeS3 | None = None) -> AwsStorage:
    return AwsStorage(
        bucket_name="private-artifacts",
        table_name="runs",
        s3_client=s3 or FakeS3(),
        dynamodb_client=dynamo,
        id_factory=lambda: stable_uuid("aws-run"),
    )


def _run_item(run_id: str, *, status: str = "running", attempt: str = "attempt") -> dict:
    return {
        "PK": {"S": f"RUN#{run_id}"},
        "SK": {"S": "META"},
        "run_id": {"S": run_id},
        "owner_sub": {"S": "operator"},
        "request_hash": {"S": "hash"},
        "metadata_json": {"S": "{}"},
        "status": {"S": status},
        "stage": {"S": "preparing"},
        "stage_events": {"L": []},
        "attempt_id": {"S": attempt},
        "lease_expires_at": {"N": str(int(NOW.timestamp()) + 60)},
        "is_public_demo": {"BOOL": False},
    }


def test_create_run_uses_transaction_and_hashed_idempotency_key() -> None:
    dynamo = RecordingDynamo()
    storage = _storage(dynamo)
    run = storage.create_or_get_run("operator", "raw-secret-key", "body-hash", {})
    method, call = dynamo.calls[0]
    assert method == "transact_write_items"
    assert len(call["TransactItems"]) == 2
    serialized = str(call)
    assert "raw-secret-key" not in serialized
    assert "IDEMP#" in serialized
    idempotency_put = call["TransactItems"][0]["Put"]
    assert "expires_at <= :now" in idempotency_put["ConditionExpression"]
    assert run.status == "pending_enqueue"


def test_claim_run_condition_compares_expiry_and_attempt() -> None:
    dynamo = RecordingDynamo()
    run_id = stable_uuid("claim-run")
    dynamo.queue_response("update_item", {"Attributes": _run_item(run_id)})
    result = _storage(dynamo).claim_run(run_id, "attempt", NOW, 90)
    _, call = dynamo.calls[0]
    condition = call["ConditionExpression"]
    assert "lease_expires_at <= :now" in condition
    assert "attempt_id = :attempt" in condition
    assert result.claimed


def test_set_stage_and_completion_require_current_attempt() -> None:
    dynamo = RecordingDynamo()
    storage = _storage(dynamo)
    run_id = stable_uuid("stage-run")
    storage.set_stage(run_id, "attempt", "detecting")
    stage_call = dynamo.calls[0][1]
    assert "attempt_id = :attempt" in stage_call["ConditionExpression"]
    assert "stage_order <= :stage_order" in stage_call["ConditionExpression"]


def test_append_stage_event_is_conditional_and_bounded() -> None:
    dynamo = RecordingDynamo()
    storage = _storage(dynamo)
    storage.append_stage_event(
        stable_uuid("event-run"),
        "attempt",
        {"event_id": stable_uuid("stage-event"), "stage": "detecting"},
    )
    _, call = dynamo.calls[0]
    assert "list_append" in call["UpdateExpression"]
    assert "stage_event_count :one" in call["UpdateExpression"]
    assert "stage_event_count < :limit" in call["ConditionExpression"]
    assert "attempt_id = :attempt" in call["ConditionExpression"]


def test_completion_requires_persisted_report(report: dict) -> None:
    dynamo = RecordingDynamo()
    storage = _storage(dynamo)
    missing_ref = storage.put_report(report)
    storage._s3.objects.clear()
    with pytest.raises(ArtifactNotFound):
        storage.complete_run(
            stable_uuid("complete-run"),
            "attempt",
            "completed",
            missing_ref,
            {"ok": True},
        )
    assert dynamo.calls == []


def test_completion_checks_artifact_then_conditionally_updates(report: dict) -> None:
    dynamo = RecordingDynamo()
    s3 = FakeS3()
    storage = _storage(dynamo, s3)
    report_ref = storage.put_report(report)
    storage.complete_run(
        stable_uuid("complete-run"),
        "attempt",
        "completed",
        report_ref,
        {"ok": True},
    )
    method, call = dynamo.calls[0]
    assert method == "update_item"
    assert call["ConditionExpression"] == "#status = :running AND attempt_id = :attempt"
    assert call["ExpressionAttributeValues"][":report_sha256"]["S"] == report_ref.sha256


def test_mark_queued_only_accepts_pending_enqueue() -> None:
    dynamo = RecordingDynamo()
    storage = _storage(dynamo)
    assert storage.mark_queued(stable_uuid("queued-run"))
    _, call = dynamo.calls[0]
    assert call["ConditionExpression"] == "#status = :pending"


def test_daily_cap_is_single_atomic_update() -> None:
    dynamo = RecordingDynamo()
    dynamo.queue_response("update_item", {"Attributes": {"count": {"N": "50"}}})
    count = _storage(dynamo).claim_daily_run(date(2026, 9, 17), 50)
    method, call = dynamo.calls[0]
    assert method == "update_item"
    assert "if_not_exists" in call["UpdateExpression"]
    assert "#count < :limit" in call["ConditionExpression"]
    assert count == 50


def test_daily_cap_conditional_failure_maps_to_limit() -> None:
    dynamo = RecordingDynamo()
    dynamo.queue_error("update_item", FakeAwsError("ConditionalCheckFailedException"))
    with pytest.raises(DailyLimitExceeded):
        _storage(dynamo).claim_daily_run(date(2026, 9, 17), 50)


def test_active_run_claim_compares_expiry_without_ttl() -> None:
    dynamo = RecordingDynamo()
    storage = _storage(dynamo)
    storage.claim_active_run("operator", "run-a", NOW, 90)
    method, call = dynamo.calls[0]
    assert method == "put_item"
    assert "expires_at <= :now" in call["ConditionExpression"]
    assert "ttl" not in str(call).lower()


def test_active_run_conditional_failure_maps_to_conflict() -> None:
    dynamo = RecordingDynamo()
    dynamo.queue_error("put_item", FakeAwsError("ConditionalCheckFailedException"))
    with pytest.raises(ActiveRunConflict):
        _storage(dynamo).claim_active_run("operator", "run-b", NOW, 90)


def test_review_is_transactionally_bound_to_current_report() -> None:
    dynamo = RecordingDynamo()
    storage = _storage(dynamo)
    storage.save_review(
        stable_uuid("review-run"),
        "trusted-actor",
        stable_uuid("report"),
        "acknowledged",
        "reviewed",
    )
    method, call = dynamo.calls[0]
    assert method == "transact_write_items"
    assert "report_id = :report_id" in str(call["TransactItems"][0])
    assert "REVIEW#" in str(call["TransactItems"][1])
    assert "trusted-actor" in str(call)


def test_stale_report_review_maps_to_review_conflict() -> None:
    dynamo = RecordingDynamo()
    dynamo.queue_error("transact_write_items", FakeAwsError("TransactionCanceledException"))
    with pytest.raises(ReviewConflict):
        _storage(dynamo).save_review(
            stable_uuid("review-run"),
            "actor",
            stable_uuid("wrong-report"),
            "acknowledged",
            "",
        )


def test_public_listing_uses_query_never_scan() -> None:
    dynamo = RecordingDynamo()
    run_id = stable_uuid("public-run")
    dynamo.queue_response(
        "query",
        {
            "Items": [
                {
                    "PK": {"S": "PUBLIC"},
                    "SK": {"S": f"DEMO#{run_id}"},
                    "run_id": {"S": run_id},
                    "summary_json": {"S": '{"label":"curated"}'},
                }
            ]
        },
    )
    result = _storage(dynamo).list_public_runs()
    assert dynamo.calls[0][0] == "query"
    assert dynamo.calls[0][1]["Limit"] == 5
    assert result[0].run_id == run_id
    assert all(method != "scan" for method, _ in dynamo.calls)


def test_public_lookup_uses_explicit_index_key() -> None:
    dynamo = RecordingDynamo()
    run_id = stable_uuid("private-run")
    dynamo.queue_response("get_item", {})
    assert _storage(dynamo).get_public_run(run_id) is None
    _, call = dynamo.calls[0]
    assert call["Key"] == {"PK": {"S": "PUBLIC"}, "SK": {"S": f"DEMO#{run_id}"}}


def test_publish_demo_uses_transaction_for_flag_and_index() -> None:
    dynamo = RecordingDynamo()
    run_id = stable_uuid("publish-run")
    _storage(dynamo).set_public_demo(run_id, {"label": "demo"})
    method, call = dynamo.calls[0]
    assert method == "transact_write_items"
    assert "is_public_demo" in str(call["TransactItems"][0])
    assert "PUBLIC" in str(call["TransactItems"][1])
