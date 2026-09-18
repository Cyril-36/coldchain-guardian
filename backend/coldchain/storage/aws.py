"""Injectable S3 and DynamoDB implementation of the canonical storage contract."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any, NoReturn, TypeVar
from uuid import UUID, uuid4

from pydantic import BaseModel

from coldchain.contracts.enums import PublicStage, ReviewDecision, RunStatus
from coldchain.contracts.schemas import (
    ArtifactRef,
    ClaimResult,
    ErrorDetail,
    Report,
    ReportSummary,
    Review,
    Run,
    RunSummary,
    Snapshot,
    StageEvent,
)

from .exceptions import (
    ActiveRunLimitExceededError,
    ConditionalCheckFailedError,
    DailyLimitExceededError,
    IdempotencyConflictError,
    NotFoundError,
    StateConflictError,
    TemporaryStorageError,
)
from .serialization import canonical_json_bytes, deserialize_verified, sha256_hex

_Model = TypeVar("_Model", bound=BaseModel)
_CONDITIONAL_CODES = {
    "ConditionalCheckFailedException",
    "TransactionCanceledException",
    "PreconditionFailed",
    "ConditionalRequestConflict",
}
_NOT_FOUND_CODES = {"NoSuchKey", "NotFound", "404"}
_TERMINAL = {RunStatus.completed, RunStatus.needs_review, RunStatus.failed}
_STAGE_ORDER = {
    PublicStage.preparing: 0,
    PublicStage.detecting: 1,
    PublicStage.collecting_evidence: 2,
    PublicStage.comparing_hypotheses: 3,
    PublicStage.verifying: 4,
    PublicStage.ready: 5,
    PublicStage.failed: 5,
}


def _error_code(error: Exception) -> str | None:
    response = getattr(error, "response", None)
    if not isinstance(response, Mapping):
        return None
    details = response.get("Error")
    if not isinstance(details, Mapping):
        return None
    code = details.get("Code")
    return code if isinstance(code, str) else None


def _s(value: str) -> dict[str, str]:
    return {"S": value}


def _n(value: int | float) -> dict[str, str]:
    return {"N": str(value)}


def _bool(value: bool) -> dict[str, bool]:
    return {"BOOL": value}


def _get_s(item: Mapping[str, Any], field: str, default: str | None = None) -> str | None:
    value = item.get(field)
    if not isinstance(value, Mapping):
        return default
    result = value.get("S")
    return result if isinstance(result, str) else default


def _get_n(item: Mapping[str, Any], field: str) -> float | None:
    value = item.get(field)
    if not isinstance(value, Mapping):
        return None
    result = value.get("N")
    return float(result) if isinstance(result, str) else None


def _get_bool(item: Mapping[str, Any], field: str, default: bool = False) -> bool:
    value = item.get(field)
    if not isinstance(value, Mapping):
        return default
    result = value.get("BOOL")
    return result if isinstance(result, bool) else default


def _get_datetime(item: Mapping[str, Any], field: str) -> datetime | None:
    text = _get_s(item, field)
    if text is not None:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    epoch = _get_n(item, field)
    return datetime.fromtimestamp(epoch, UTC) if epoch is not None else None


def _json(value: BaseModel | Mapping[str, Any]) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def _artifact_id(reference: ArtifactRef, prefix: str) -> str:
    expected_prefix = f"{prefix}/"
    if not reference.key.startswith(expected_prefix) or not reference.key.endswith(".json"):
        raise ValueError("artifact reference key is not canonical")
    artifact_id = reference.key[len(expected_prefix) : -5]
    UUID(artifact_id)
    return artifact_id


class AwsStorage:
    """S3 artifacts and DynamoDB metadata with clients injected for offline tests."""

    def __init__(
        self,
        *,
        bucket_name: str,
        table_name: str,
        s3_client: Any | None = None,
        dynamodb_client: Any | None = None,
        snapshot_validator: Callable[[Any], Snapshot] = Snapshot.model_validate,
        report_validator: Callable[[Any], Report] = Report.model_validate,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        daily_run_limit: int = 50,
        active_run_lease_seconds: int = 300,
    ) -> None:
        if daily_run_limit <= 0 or active_run_lease_seconds <= 0:
            raise ValueError("storage limits must be positive")
        if s3_client is None or dynamodb_client is None:
            import boto3

            s3_client = s3_client or boto3.client("s3")
            dynamodb_client = dynamodb_client or boto3.client("dynamodb")
        self._bucket_name = bucket_name
        self._table_name = table_name
        self._s3 = s3_client
        self._dynamodb = dynamodb_client
        self._snapshot_validator = snapshot_validator
        self._report_validator = report_validator
        self._id_factory = id_factory or (lambda: str(uuid4()))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._daily_run_limit = daily_run_limit
        self._active_run_lease_seconds = active_run_lease_seconds

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _run_key(run_id: str) -> dict[str, dict[str, str]]:
        return {"PK": _s(f"RUN#{run_id}"), "SK": _s("META")}

    @classmethod
    def _idempotency_key(
        cls, owner_sub: str, idempotency_key: str
    ) -> dict[str, dict[str, str]]:
        return {
            "PK": _s(f"IDEMP#{cls._hash(owner_sub)}#{cls._hash(idempotency_key)}"),
            "SK": _s("REQUEST"),
        }

    @classmethod
    def _active_key(cls, owner_sub: str) -> dict[str, dict[str, str]]:
        return {"PK": _s(f"ACTIVE#{cls._hash(owner_sub)}"), "SK": _s("LEASE")}

    @staticmethod
    def _public_key(run_id: str) -> dict[str, dict[str, str]]:
        return {"PK": _s("PUBLIC"), "SK": _s(f"DEMO#{run_id}")}

    @staticmethod
    def _raise_provider(operation: str, error: Exception) -> NoReturn:
        raise TemporaryStorageError(f"{operation} failed") from error

    def _decode_run(self, item: Mapping[str, Any]) -> Run:
        run_id = _get_s(item, "run_id")
        snapshot_id = _get_s(item, "snapshot_id")
        shipment_id = _get_s(item, "shipment_id")
        created_epoch = _get_n(item, "created_at")
        if None in {run_id, snapshot_id, shipment_id, created_epoch}:
            raise TemporaryStorageError("stored run record is incomplete")

        def ref(prefix: str) -> ArtifactRef | None:
            key = _get_s(item, f"{prefix}_key")
            digest = _get_s(item, f"{prefix}_sha256")
            return ArtifactRef(key=key, sha256=digest) if key and digest else None

        raw_events = item.get("stage_events", {"L": []})
        values = raw_events.get("L", []) if isinstance(raw_events, Mapping) else []
        events = [
            StageEvent.model_validate_json(entry["S"])
            for entry in values
            if isinstance(entry, Mapping) and isinstance(entry.get("S"), str)
        ]
        review_json = _get_s(item, "review_json")
        summary_json = _get_s(item, "report_summary_json")
        error_json = _get_s(item, "error_json")
        lease_epoch = _get_n(item, "lease_expires_at")
        seed = _get_n(item, "seed")
        completed_epoch = _get_n(item, "completed_at")
        return Run(
            run_id=run_id,
            owner_sub=_get_s(item, "owner_sub"),
            status=_get_s(item, "status", RunStatus.pending_enqueue.value),
            stage=_get_s(item, "stage", PublicStage.preparing.value),
            created_at=datetime.fromtimestamp(created_epoch, UTC),
            completed_at=(
                datetime.fromtimestamp(completed_epoch, UTC)
                if completed_epoch is not None
                else None
            ),
            report_id=_get_s(item, "report_id"),
            review=Review.model_validate_json(review_json) if review_json else None,
            generation_mode=_get_s(item, "generation_mode"),
            shipment_id=shipment_id,
            snapshot_id=snapshot_id,
            stage_events=events,
            report_summary=(
                ReportSummary.model_validate_json(summary_json) if summary_json else None
            ),
            error=ErrorDetail.model_validate_json(error_json) if error_json else None,
            is_public_demo=_get_bool(item, "is_public_demo"),
            label=_get_s(item, "label"),
            snapshot_ref=ref("snapshot"),
            report_ref=ref("report"),
            scenario_id=_get_s(item, "scenario_id"),
            attempt_id=_get_s(item, "attempt_id"),
            lease_expires_at=(
                datetime.fromtimestamp(lease_epoch, UTC) if lease_epoch is not None else None
            ),
            seed=int(seed) if seed is not None else None,
            base_timestamp=_get_datetime(item, "base_timestamp"),
        )

    def create_or_get_run(
        self,
        owner_sub: str,
        idempotency_key: str,
        request_hash: str,
        metadata: dict[str, Any],
    ) -> Run:
        run_id = str(metadata.get("run_id") or self._id_factory())
        snapshot_id = str(metadata.get("snapshot_id") or self._id_factory())
        shipment_id = str(metadata.get("shipment_id") or self._id_factory())
        now = self._clock()
        now_epoch = int(now.timestamp())
        expires_at = int((now + timedelta(hours=24)).timestamp())
        active_expires = int(
            (now + timedelta(seconds=self._active_run_lease_seconds)).timestamp()
        )
        run_item: dict[str, Any] = {
            **self._run_key(run_id),
            "run_id": _s(run_id),
            "owner_sub": _s(owner_sub),
            "request_hash": _s(request_hash),
            "snapshot_id": _s(snapshot_id),
            "shipment_id": _s(shipment_id),
            "created_at": _n(now_epoch),
            "status": _s(RunStatus.pending_enqueue.value),
            "stage": _s(PublicStage.preparing.value),
            "stage_order": _n(0),
            "stage_events": {"L": []},
            "stage_event_count": _n(0),
            "is_public_demo": _bool(bool(metadata.get("is_public_demo", False))),
        }
        for field in ("scenario_id", "label"):
            if metadata.get(field) is not None:
                run_item[field] = _s(str(metadata[field]))
        if metadata.get("seed") is not None:
            run_item["seed"] = _n(int(metadata["seed"]))
        if metadata.get("base_timestamp") is not None:
            base = metadata["base_timestamp"]
            if isinstance(base, str):
                base = datetime.fromisoformat(base.replace("Z", "+00:00"))
            run_item["base_timestamp"] = _s(base.astimezone(UTC).isoformat())
        reserved_run = self._decode_run(run_item)
        idem_key = self._idempotency_key(owner_sub, idempotency_key)
        try:
            self._dynamodb.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": {
                                **idem_key,
                                "request_hash": _s(request_hash),
                                "run_id": _s(run_id),
                                "expires_at": _n(expires_at),
                            },
                            "ConditionExpression": "attribute_not_exists(PK) OR expires_at <= :now",
                            "ExpressionAttributeValues": {":now": _n(now_epoch)},
                        }
                    },
                    {
                        "Update": {
                            "TableName": self._table_name,
                            "Key": {
                                "PK": _s(f"LIMIT#{now.astimezone(UTC).date().isoformat()}"),
                                "SK": _s("COUNT"),
                            },
                            "UpdateExpression": "SET #count = if_not_exists(#count, :zero) + :one",
                            "ConditionExpression": (
                                "attribute_not_exists(#count) OR #count < :limit"
                            ),
                            "ExpressionAttributeNames": {"#count": "count"},
                            "ExpressionAttributeValues": {
                                ":zero": _n(0),
                                ":one": _n(1),
                                ":limit": _n(self._daily_run_limit),
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": {
                                **self._active_key(owner_sub),
                                "owner_sub": _s(owner_sub),
                                "run_id": _s(run_id),
                                "expires_at": _n(active_expires),
                            },
                            "ConditionExpression": "attribute_not_exists(PK) OR expires_at <= :now",
                            "ExpressionAttributeValues": {":now": _n(now_epoch)},
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": run_item,
                            "ConditionExpression": "attribute_not_exists(PK)",
                        }
                    },
                ]
            )
            return reserved_run
        except Exception as error:
            if _error_code(error) not in _CONDITIONAL_CODES:
                self._raise_provider("create run", error)
            reasons = getattr(error, "response", {}).get("CancellationReasons", [])
            if isinstance(reasons, list) and len(reasons) >= 3:
                idempotency_failed = isinstance(reasons[0], Mapping) and reasons[0].get(
                    "Code"
                ) not in {None, "None"}
                if (
                    not idempotency_failed
                    and isinstance(reasons[1], Mapping)
                    and reasons[1].get("Code") not in {None, "None"}
                ):
                    raise DailyLimitExceededError("UTC daily run limit reached") from error
                if (
                    not idempotency_failed
                    and isinstance(reasons[2], Mapping)
                    and reasons[2].get("Code") not in {None, "None"}
                ):
                    raise ActiveRunLimitExceededError(
                        "operator already has an active run"
                    ) from error
        try:
            response = self._dynamodb.get_item(
                TableName=self._table_name, Key=idem_key, ConsistentRead=True
            )
        except Exception as error:
            self._raise_provider("read idempotency mapping", error)
        existing = response.get("Item")
        if not isinstance(existing, Mapping):
            raise ConditionalCheckFailedError("run reservation conflicted")
        if _get_s(existing, "request_hash") != request_hash:
            raise IdempotencyConflictError(
                "idempotency key was reused with different request content"
            )
        run = self.get_run(_get_s(existing, "run_id", "") or "")
        if run is None:
            raise TemporaryStorageError("idempotency mapping references a missing run")
        return run

    def get_run(self, run_id: str) -> Run | None:
        try:
            response = self._dynamodb.get_item(
                TableName=self._table_name,
                Key=self._run_key(run_id),
                ConsistentRead=True,
            )
        except Exception as error:
            self._raise_provider("read run", error)
        item = response.get("Item")
        return self._decode_run(item) if isinstance(item, Mapping) else None

    def mark_queued(self, run_id: str) -> None:
        try:
            self._dynamodb.update_item(
                TableName=self._table_name,
                Key=self._run_key(run_id),
                UpdateExpression="SET #status = :queued",
                ConditionExpression="#status = :pending AND attribute_exists(snapshot_key)",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":pending": _s(RunStatus.pending_enqueue.value),
                    ":queued": _s(RunStatus.queued.value),
                },
            )
            return
        except Exception as error:
            if _error_code(error) not in _CONDITIONAL_CODES:
                self._raise_provider("mark run queued", error)
        run = self.get_run(run_id)
        if run is None:
            raise NotFoundError("run does not exist")
        if run.status in {RunStatus.queued, RunStatus.running, *_TERMINAL}:
            return
        raise ConditionalCheckFailedError("run cannot be queued before snapshot attachment")

    def claim_run(
        self, run_id: str, attempt_id: str, now: datetime, lease_seconds: int
    ) -> ClaimResult:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        try:
            self._dynamodb.update_item(
                TableName=self._table_name,
                Key=self._run_key(run_id),
                UpdateExpression=(
                    "SET #status = :running, attempt_id = :attempt, "
                    "lease_expires_at = :expires"
                ),
                ConditionExpression=(
                    "#status IN (:pending, :queued) OR "
                    "(#status = :running AND (lease_expires_at <= :now OR attempt_id = :attempt))"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":pending": _s(RunStatus.pending_enqueue.value),
                    ":queued": _s(RunStatus.queued.value),
                    ":running": _s(RunStatus.running.value),
                    ":attempt": _s(attempt_id),
                    ":now": _n(int(now.timestamp())),
                    ":expires": _n(int((now + timedelta(seconds=lease_seconds)).timestamp())),
                },
            )
            return ClaimResult(success=True, reason="claimed")
        except Exception as error:
            if _error_code(error) not in _CONDITIONAL_CODES:
                self._raise_provider("claim run", error)
        run = self.get_run(run_id)
        if run is None:
            raise NotFoundError("run does not exist")
        return ClaimResult(
            success=False,
            reason="terminal" if run.status in _TERMINAL else "active_lease",
        )

    def set_stage(self, run_id: str, attempt_id: str, stage: PublicStage) -> None:
        stage = PublicStage(stage)
        try:
            self._dynamodb.update_item(
                TableName=self._table_name,
                Key=self._run_key(run_id),
                UpdateExpression="SET stage = :stage, stage_order = :stage_order",
                ConditionExpression=(
                    "#status = :running AND attempt_id = :attempt AND stage_order <= :stage_order"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":running": _s(RunStatus.running.value),
                    ":attempt": _s(attempt_id),
                    ":stage": _s(stage.value),
                    ":stage_order": _n(_STAGE_ORDER[stage]),
                },
            )
        except Exception as error:
            if _error_code(error) in _CONDITIONAL_CODES:
                raise ConditionalCheckFailedError("worker attempt or stage is stale") from error
            self._raise_provider("set run stage", error)

    def append_stage_event(
        self, run_id: str, attempt_id: str, event: StageEvent
    ) -> None:
        value = StageEvent.model_validate(event)
        try:
            self._dynamodb.update_item(
                TableName=self._table_name,
                Key=self._run_key(run_id),
                UpdateExpression=(
                    "SET stage_events = list_append(if_not_exists(stage_events, :empty), :event) "
                    "ADD stage_event_count :one"
                ),
                ConditionExpression=(
                    "#status = :running AND attempt_id = :attempt AND "
                    "(attribute_not_exists(stage_event_count) OR stage_event_count < :limit)"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":running": _s(RunStatus.running.value),
                    ":attempt": _s(attempt_id),
                    ":empty": {"L": []},
                    ":event": {"L": [_s(value.model_dump_json())]},
                    ":limit": _n(40),
                    ":one": _n(1),
                },
            )
        except Exception as error:
            if _error_code(error) in _CONDITIONAL_CODES:
                raise ConditionalCheckFailedError(
                    "worker attempt or event limit is stale"
                ) from error
            self._raise_provider("append stage event", error)

    def complete_run(
        self,
        run_id: str,
        attempt_id: str,
        status: RunStatus,
        report_ref: ArtifactRef | None,
        summary: str,
    ) -> None:
        status = RunStatus(status)
        if status not in _TERMINAL:
            raise ValueError("completion status must be terminal")
        values: dict[str, Any] = {
            ":running": _s(RunStatus.running.value),
            ":attempt": _s(attempt_id),
            ":terminal": _s(status.value),
            ":stage": _s(
                PublicStage.failed.value if status == RunStatus.failed else PublicStage.ready.value
            ),
            ":stage_order": _n(5),
            ":completed": _n(int(self._clock().timestamp())),
        }
        expression = (
            "SET #status = :terminal, stage = :stage, stage_order = :stage_order, "
            "completed_at = :completed"
        )
        if report_ref is not None:
            report = self.get_report(report_ref)
            expression += (
                ", report_key = :report_key, report_sha256 = :report_sha256, "
                "report_id = :report_id, generation_mode = :generation_mode, "
                "report_summary_json = :report_summary"
            )
            values.update(
                {
                    ":report_key": _s(report_ref.key),
                    ":report_sha256": _s(report_ref.sha256),
                    ":report_id": _s(report.report_id),
                    ":generation_mode": _s(report.generation_mode.value),
                    ":report_summary": _s(
                        ReportSummary(
                            outcome=report.outcome,
                            primary_hypothesis=report.primary_hypothesis,
                            review_required=report.review_required,
                        ).model_dump_json()
                    ),
                }
            )
        elif status == RunStatus.failed:
            expression += ", error_json = :error"
            values[":error"] = _s(
                ErrorDetail(
                    code="worker_failed",
                    message=summary,
                    request_id=run_id,
                    retryable=False,
                ).model_dump_json()
            )
        else:
            raise ConditionalCheckFailedError("successful completion requires a report")
        try:
            self._dynamodb.update_item(
                TableName=self._table_name,
                Key=self._run_key(run_id),
                UpdateExpression=expression,
                ConditionExpression="#status = :running AND attempt_id = :attempt",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues=values,
            )
            return
        except Exception as error:
            if _error_code(error) not in _CONDITIONAL_CODES:
                self._raise_provider("complete run", error)
        run = self.get_run(run_id)
        if (
            run
            and run.status == status
            and run.attempt_id == attempt_id
            and run.report_ref == report_ref
        ):
            return
        raise ConditionalCheckFailedError("run completion is stale or contradictory")

    def _put_artifact(
        self,
        artifact_id: str,
        prefix: str,
        value: _Model,
        validator: Callable[[Any], _Model],
    ) -> ArtifactRef:
        UUID(artifact_id)
        payload = canonical_json_bytes(validator(value))
        key = f"{prefix}/{artifact_id}.json"
        reference = ArtifactRef(key=key, sha256=sha256_hex(payload))
        try:
            self._s3.put_object(
                Bucket=self._bucket_name,
                Key=key,
                Body=payload,
                ContentType="application/json",
                IfNoneMatch="*",
            )
            return reference
        except Exception as error:
            if _error_code(error) not in _CONDITIONAL_CODES:
                self._raise_provider("write artifact", error)
        try:
            response = self._s3.get_object(Bucket=self._bucket_name, Key=key)
            body = response["Body"]
            existing = body.read() if hasattr(body, "read") else bytes(body)
        except Exception as error:
            self._raise_provider("read conflicting artifact", error)
        if existing != payload:
            raise StateConflictError("artifact key already stores different bytes")
        return reference

    def _get_artifact(
        self,
        reference: ArtifactRef,
        prefix: str,
        validator: Callable[[Any], _Model],
    ) -> _Model:
        _artifact_id(reference, prefix)
        try:
            response = self._s3.get_object(Bucket=self._bucket_name, Key=reference.key)
            body = response["Body"]
            payload = body.read() if hasattr(body, "read") else bytes(body)
        except Exception as error:
            if _error_code(error) in _NOT_FOUND_CODES:
                raise NotFoundError("artifact does not exist") from error
            self._raise_provider("read artifact", error)
        return deserialize_verified(payload, reference.sha256, validator)

    def put_snapshot(self, snapshot: Snapshot) -> ArtifactRef:
        value = self._snapshot_validator(snapshot)
        return self._put_artifact(value.snapshot_id, "snapshots", value, self._snapshot_validator)

    def get_snapshot(self, snapshot_ref: ArtifactRef) -> Snapshot:
        return self._get_artifact(snapshot_ref, "snapshots", self._snapshot_validator)

    def attach_snapshot(self, run_id: str, snapshot_ref: ArtifactRef) -> None:
        snapshot = self.get_snapshot(snapshot_ref)
        run = self.get_run(run_id)
        if run is None:
            raise NotFoundError("run does not exist")
        if snapshot.snapshot_id != run.snapshot_id:
            raise ConditionalCheckFailedError("snapshot does not match reserved snapshot_id")
        try:
            self._dynamodb.update_item(
                TableName=self._table_name,
                Key=self._run_key(run_id),
                UpdateExpression="SET snapshot_key = :key, snapshot_sha256 = :sha256",
                ConditionExpression=(
                    "#status = :pending AND (attribute_not_exists(snapshot_key) OR "
                    "(snapshot_key = :key AND snapshot_sha256 = :sha256))"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":pending": _s(RunStatus.pending_enqueue.value),
                    ":key": _s(snapshot_ref.key),
                    ":sha256": _s(snapshot_ref.sha256),
                },
            )
            return
        except Exception as error:
            if _error_code(error) not in _CONDITIONAL_CODES:
                self._raise_provider("attach snapshot", error)
        current = self.get_run(run_id)
        if current is not None and current.snapshot_ref == snapshot_ref:
            return
        raise ConditionalCheckFailedError("snapshot attachment is stale or contradictory")

    def put_report(self, report: Report) -> ArtifactRef:
        value = self._report_validator(report)
        return self._put_artifact(value.report_id, "reports", value, self._report_validator)

    def get_report(self, report_ref: ArtifactRef) -> Report:
        return self._get_artifact(report_ref, "reports", self._report_validator)

    def create_report_download_url(
        self, report_ref: ArtifactRef, expires_in_seconds: int = 300
    ) -> str:
        _artifact_id(report_ref, "reports")
        if not 1 <= expires_in_seconds <= 300:
            raise ValueError("report URL expiry must be between 1 and 300 seconds")
        try:
            return self._s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket_name, "Key": report_ref.key},
                ExpiresIn=expires_in_seconds,
            )
        except Exception as error:
            self._raise_provider("sign report download", error)

    def claim_daily_run(self, utc_date: date, limit: int = 50) -> int:
        try:
            response = self._dynamodb.update_item(
                TableName=self._table_name,
                Key={"PK": _s(f"LIMIT#{utc_date.isoformat()}"), "SK": _s("COUNT")},
                UpdateExpression="SET #count = if_not_exists(#count, :zero) + :one",
                ConditionExpression="attribute_not_exists(#count) OR #count < :limit",
                ExpressionAttributeNames={"#count": "count"},
                ExpressionAttributeValues={
                    ":zero": _n(0),
                    ":one": _n(1),
                    ":limit": _n(limit),
                },
                ReturnValues="ALL_NEW",
            )
            return int(_get_n(response["Attributes"], "count") or 0)
        except Exception as error:
            if _error_code(error) in _CONDITIONAL_CODES:
                raise DailyLimitExceededError("UTC daily run limit reached") from error
            self._raise_provider("claim daily run capacity", error)

    def claim_active_run(
        self, owner_sub: str, run_id: str, now: datetime, lease_seconds: int
    ) -> tuple[str, datetime]:
        expires = now + timedelta(seconds=lease_seconds)
        try:
            self._dynamodb.put_item(
                TableName=self._table_name,
                Item={
                    **self._active_key(owner_sub),
                    "owner_sub": _s(owner_sub),
                    "run_id": _s(run_id),
                    "expires_at": _n(int(expires.timestamp())),
                },
                ConditionExpression=(
                    "attribute_not_exists(PK) OR expires_at <= :now OR run_id = :run_id"
                ),
                ExpressionAttributeValues={
                    ":now": _n(int(now.timestamp())),
                    ":run_id": _s(run_id),
                },
            )
            return run_id, expires
        except Exception as error:
            if _error_code(error) in _CONDITIONAL_CODES:
                raise ActiveRunLimitExceededError(
                    "operator already has an active run"
                ) from error
            self._raise_provider("claim active run", error)

    def release_active_run(self, owner_sub: str, run_id: str) -> bool:
        try:
            self._dynamodb.delete_item(
                TableName=self._table_name,
                Key=self._active_key(owner_sub),
                ConditionExpression="run_id = :run_id",
                ExpressionAttributeValues={":run_id": _s(run_id)},
            )
            return True
        except Exception as error:
            if _error_code(error) in _CONDITIONAL_CODES:
                return False
            self._raise_provider("release active run", error)

    def save_review(
        self,
        run_id: str,
        actor_sub: str,
        report_id: str,
        decision: ReviewDecision,
        note: str,
    ) -> Review:
        review = Review(
            review_id=self._id_factory(),
            run_id=run_id,
            actor_sub=actor_sub,
            report_id=report_id,
            decision=decision,
            note=note,
            reviewed_at=self._clock(),
        )
        try:
            self._dynamodb.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self._table_name,
                            "Key": self._run_key(run_id),
                            "UpdateExpression": "SET review_json = :review",
                            "ConditionExpression": "report_id = :report_id",
                            "ExpressionAttributeValues": {
                                ":report_id": _s(report_id),
                                ":review": _s(review.model_dump_json()),
                            },
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": {
                                "PK": _s(f"RUN#{run_id}"),
                                "SK": _s(f"REVIEW#{review.review_id}"),
                                "review_json": _s(review.model_dump_json()),
                            },
                            "ConditionExpression": "attribute_not_exists(SK)",
                        }
                    },
                ]
            )
            return review
        except Exception as error:
            if _error_code(error) in _CONDITIONAL_CODES:
                raise StateConflictError(
                    "review does not reference the current report"
                ) from error
            self._raise_provider("save review", error)

    def set_public_demo(self, run_id: str, summary: Mapping[str, Any]) -> None:
        value = RunSummary.model_validate(summary)
        try:
            self._dynamodb.transact_write_items(
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self._table_name,
                            "Key": self._run_key(run_id),
                            "UpdateExpression": "SET is_public_demo = :true",
                            "ConditionExpression": "attribute_exists(PK)",
                            "ExpressionAttributeValues": {":true": _bool(True)},
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": {
                                **self._public_key(run_id),
                                "run_id": _s(run_id),
                                "summary_json": _s(value.model_dump_json()),
                            },
                        }
                    },
                ]
            )
        except Exception as error:
            if _error_code(error) in _CONDITIONAL_CODES:
                raise ConditionalCheckFailedError("public demo update conflicted") from error
            self._raise_provider("publish demo run", error)

    def list_public_runs(self) -> list[RunSummary]:
        try:
            response = self._dynamodb.query(
                TableName=self._table_name,
                KeyConditionExpression="PK = :public AND begins_with(SK, :demo)",
                ExpressionAttributeValues={":public": _s("PUBLIC"), ":demo": _s("DEMO#")},
                Limit=5,
            )
        except Exception as error:
            self._raise_provider("list public runs", error)
        return [
            RunSummary.model_validate_json(_get_s(item, "summary_json", "{}") or "{}")
            for item in response.get("Items", [])
        ]

    def get_public_run(self, run_id: str) -> Run | None:
        run = self.get_run(run_id)
        return run if run is not None and run.is_public_demo else None
