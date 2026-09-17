"""Injectable S3 and DynamoDB storage adapter.

The adapter uses low-level boto3 clients. Tests pass hand-written fakes, so
importing this module never discovers credentials or contacts AWS.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any, NoReturn
from uuid import UUID, uuid4

from coldchain.simulator.validation import normalize_snapshot

from .exceptions import (
    ActiveRunConflict,
    ArtifactNotFound,
    ConditionalWriteConflict,
    DailyLimitExceeded,
    IdempotencyConflict,
    ImmutableArtifactConflict,
    LeaseConflict,
    ReviewConflict,
    RunNotFound,
    StorageUnavailable,
)
from .models import (
    TERMINAL_STATUSES,
    ActiveRunLease,
    ArtifactRef,
    ClaimResult,
    ReviewRecord,
    RunRecord,
    RunStage,
    RunStatus,
    RunSummary,
)
from .serialization import (
    ArtifactValidator,
    canonical_json_bytes,
    copy_mapping,
    deserialize_verified,
    sha256_hex,
)

_CONDITIONAL_CODES = {
    "ConditionalCheckFailedException",
    "TransactionCanceledException",
    "PreconditionFailed",
    "ConditionalRequestConflict",
}
_NOT_FOUND_CODES = {"NoSuchKey", "NotFound", "404"}
_STAGE_ORDER = {
    "preparing": 0,
    "detecting": 1,
    "collecting_evidence": 2,
    "comparing_hypotheses": 3,
    "verifying": 4,
    "ready": 5,
    "failed": 5,
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


def _json(value: Mapping[str, Any]) -> str:
    return canonical_json_bytes(value).decode("utf-8")


class AwsStorage:
    """S3 artifact and DynamoDB metadata adapter with injectable clients."""

    def __init__(
        self,
        *,
        bucket_name: str,
        table_name: str,
        s3_client: Any | None = None,
        dynamodb_client: Any | None = None,
        snapshot_validator: ArtifactValidator = normalize_snapshot,
        report_validator: ArtifactValidator = copy_mapping,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        daily_run_limit: int = 50,
        active_run_lease_seconds: int = 300,
    ) -> None:
        if daily_run_limit <= 0:
            raise ValueError("daily_run_limit must be positive")
        if active_run_lease_seconds <= 0:
            raise ValueError("active_run_lease_seconds must be positive")
        if s3_client is None or dynamodb_client is None:
            import boto3  # Imported lazily; constructor injection avoids this in tests.

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
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _run_key(run_id: str) -> dict[str, dict[str, str]]:
        return {"PK": _s(f"RUN#{run_id}"), "SK": _s("META")}

    @classmethod
    def _idempotency_key(cls, owner_sub: str, idempotency_key: str) -> dict[str, dict[str, str]]:
        return {
            "PK": _s(f"IDEMP#{cls._hash(owner_sub)}#{cls._hash(idempotency_key)}"),
            "SK": _s("REQUEST"),
        }

    @staticmethod
    def _active_key(owner_sub: str) -> dict[str, dict[str, str]]:
        owner_hash = hashlib.sha256(owner_sub.encode("utf-8")).hexdigest()
        return {"PK": _s(f"ACTIVE#{owner_hash}"), "SK": _s("LEASE")}

    @staticmethod
    def _public_key(run_id: str) -> dict[str, dict[str, str]]:
        return {"PK": _s("PUBLIC"), "SK": _s(f"DEMO#{run_id}")}

    @staticmethod
    def _raise_provider(operation: str, error: Exception) -> NoReturn:
        raise StorageUnavailable(f"{operation} failed") from error

    def _decode_run(self, item: Mapping[str, Any]) -> RunRecord:
        run_id = _get_s(item, "run_id")
        owner_sub = _get_s(item, "owner_sub")
        request_hash = _get_s(item, "request_hash")
        if run_id is None or owner_sub is None or request_hash is None:
            raise StorageUnavailable("stored run record is incomplete")
        metadata = json.loads(_get_s(item, "metadata_json", "{}") or "{}")
        raw_events = item.get("stage_events", {"L": []})
        event_values = raw_events.get("L", []) if isinstance(raw_events, Mapping) else []
        stage_events = [
            json.loads(value["S"])
            for value in event_values
            if isinstance(value, Mapping) and isinstance(value.get("S"), str)
        ]
        report_key = _get_s(item, "report_key")
        report_ref = None
        if report_key is not None:
            report_ref = ArtifactRef(
                key=report_key,
                sha256=_get_s(item, "report_sha256", "") or "",
                artifact_id=_get_s(item, "report_id", "") or "",
            )
        snapshot_key = _get_s(item, "snapshot_key")
        snapshot_ref = None
        if snapshot_key is not None:
            snapshot_ref = ArtifactRef(
                key=snapshot_key,
                sha256=_get_s(item, "snapshot_sha256", "") or "",
                artifact_id=_get_s(item, "snapshot_id", "") or "",
            )
        lease_epoch = _get_n(item, "lease_expires_at")
        return RunRecord(
            run_id=run_id,
            owner_sub=owner_sub,
            request_hash=request_hash,
            metadata=metadata,
            status=_get_s(item, "status", "pending_enqueue"),  # type: ignore[arg-type]
            stage=_get_s(item, "stage", "preparing"),  # type: ignore[arg-type]
            attempt_id=_get_s(item, "attempt_id"),
            lease_expires_at=(
                datetime.fromtimestamp(lease_epoch, UTC) if lease_epoch is not None else None
            ),
            stage_events=stage_events,
            snapshot_ref=snapshot_ref,
            report_ref=report_ref,
            summary=json.loads(_get_s(item, "summary_json", "null") or "null"),
            is_public_demo=_get_bool(item, "is_public_demo"),
        )

    def create_or_get_run(
        self,
        owner_sub: str,
        idempotency_key: str,
        request_hash: str,
        metadata: Mapping[str, Any],
    ) -> RunRecord:
        """Atomically reserve idempotency, daily capacity, active ownership, and run."""

        run_id = str(metadata.get("run_id") or self._id_factory())
        idem_key = self._idempotency_key(owner_sub, idempotency_key)
        reservation_time = self._clock()
        now_epoch = int(reservation_time.timestamp())
        expires_at = int((reservation_time + timedelta(hours=24)).timestamp())
        active_expires_at = int(
            (reservation_time + timedelta(seconds=self._active_run_lease_seconds)).timestamp()
        )
        utc_date = reservation_time.astimezone(UTC).date()
        run_item = {
            **self._run_key(run_id),
            "run_id": _s(run_id),
            "owner_sub": _s(owner_sub),
            "request_hash": _s(request_hash),
            "metadata_json": _s(_json(metadata)),
            "status": _s("pending_enqueue"),
            "stage": _s("preparing"),
            "stage_order": _n(0),
            "stage_events": {"L": []},
            "stage_event_count": _n(0),
            "is_public_demo": _bool(False),
        }
        idem_item = {
            **idem_key,
            "request_hash": _s(request_hash),
            "run_id": _s(run_id),
            "expires_at": _n(expires_at),
        }
        active_item = {
            **self._active_key(owner_sub),
            "owner_sub": _s(owner_sub),
            "run_id": _s(run_id),
            "expires_at": _n(active_expires_at),
        }
        try:
            self._dynamodb.transact_write_items(
                TransactItems=[
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": idem_item,
                            "ConditionExpression": (
                                "attribute_not_exists(PK) OR expires_at <= :now"
                            ),
                            "ExpressionAttributeValues": {":now": _n(now_epoch)},
                        }
                    },
                    {
                        "Update": {
                            "TableName": self._table_name,
                            "Key": {
                                "PK": _s(f"LIMIT#{utc_date.isoformat()}"),
                                "SK": _s("COUNT"),
                            },
                            "UpdateExpression": (
                                "SET #count = if_not_exists(#count, :zero) + :one"
                            ),
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
                            "Item": active_item,
                            "ConditionExpression": (
                                "attribute_not_exists(PK) OR expires_at <= :now"
                            ),
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
            return self._decode_run(run_item)
        except Exception as error:
            if _error_code(error) not in _CONDITIONAL_CODES:
                self._raise_provider("create run", error)
            response = getattr(error, "response", {})
            reasons = response.get("CancellationReasons", [])
            if isinstance(reasons, list) and len(reasons) >= 3:
                idempotency_reason = reasons[0]
                daily_reason = reasons[1]
                active_reason = reasons[2]
                idempotency_failed = (
                    isinstance(idempotency_reason, Mapping)
                    and idempotency_reason.get("Code") not in {None, "None"}
                )
                if not idempotency_failed:
                    if isinstance(daily_reason, Mapping) and daily_reason.get(
                        "Code"
                    ) not in {None, "None"}:
                        raise DailyLimitExceeded("UTC daily run limit reached") from error
                    if isinstance(active_reason, Mapping) and active_reason.get(
                        "Code"
                    ) not in {None, "None"}:
                        raise ActiveRunConflict(
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
            raise ConditionalWriteConflict("run reservation conflicted")
        if _get_s(existing, "request_hash") != request_hash:
            raise IdempotencyConflict(
                "idempotency key was already used with different request content"
            )
        existing_run_id = _get_s(existing, "run_id")
        run = self.get_run(existing_run_id or "")
        if run is None:
            raise StorageUnavailable("idempotency mapping references a missing run")
        return run

    def get_run(self, run_id: str) -> RunRecord | None:
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

    def mark_queued(self, run_id: str) -> bool:
        try:
            self._dynamodb.update_item(
                TableName=self._table_name,
                Key=self._run_key(run_id),
                UpdateExpression="SET #status = :queued",
                ConditionExpression="#status = :pending AND attribute_exists(snapshot_key)",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":pending": _s("pending_enqueue"),
                    ":queued": _s("queued"),
                },
            )
            return True
        except Exception as error:
            if _error_code(error) not in _CONDITIONAL_CODES:
                self._raise_provider("mark run queued", error)
        run = self.get_run(run_id)
        return run is not None and run.status == "queued"

    def claim_run(
        self,
        run_id: str,
        attempt_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ClaimResult:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        values = {
            ":pending": _s("pending_enqueue"),
            ":queued": _s("queued"),
            ":running": _s("running"),
            ":attempt": _s(attempt_id),
            ":now": _n(int(now.timestamp())),
            ":expires": _n(int((now + timedelta(seconds=lease_seconds)).timestamp())),
        }
        try:
            response = self._dynamodb.update_item(
                TableName=self._table_name,
                Key=self._run_key(run_id),
                UpdateExpression=(
                    "SET #status = :running, attempt_id = :attempt, lease_expires_at = :expires"
                ),
                ConditionExpression=(
                    "#status IN (:pending, :queued) OR "
                    "(#status = :running AND "
                    "(lease_expires_at <= :now OR attempt_id = :attempt))"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )
            return ClaimResult(True, self._decode_run(response["Attributes"]))
        except Exception as error:
            if _error_code(error) in _CONDITIONAL_CODES:
                run = self.get_run(run_id)
                if run is not None and run.status in TERMINAL_STATUSES:
                    return ClaimResult(False, run, "terminal")
                raise LeaseConflict("run has an active worker lease") from error
            self._raise_provider("claim run", error)

    def set_stage(self, run_id: str, attempt_id: str, stage: RunStage) -> None:
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
                    ":running": _s("running"),
                    ":attempt": _s(attempt_id),
                    ":stage": _s(stage),
                    ":stage_order": _n(_STAGE_ORDER[stage]),
                },
            )
        except Exception as error:
            if _error_code(error) in _CONDITIONAL_CODES:
                raise ConditionalWriteConflict("worker attempt or stage is stale") from error
            self._raise_provider("set run stage", error)

    def append_stage_event(
        self,
        run_id: str,
        attempt_id: str,
        event: Mapping[str, Any],
    ) -> None:
        try:
            self._dynamodb.update_item(
                TableName=self._table_name,
                Key=self._run_key(run_id),
                UpdateExpression=(
                    "SET stage_events = list_append("
                    "if_not_exists(stage_events, :empty), :event) "
                    "ADD stage_event_count :one"
                ),
                ConditionExpression=(
                    "#status = :running AND attempt_id = :attempt "
                    "AND (attribute_not_exists(stage_event_count) OR stage_event_count < :limit)"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":running": _s("running"),
                    ":attempt": _s(attempt_id),
                    ":empty": {"L": []},
                    ":event": {"L": [_s(_json(event))]},
                    ":limit": _n(40),
                    ":one": _n(1),
                },
            )
        except Exception as error:
            if _error_code(error) in _CONDITIONAL_CODES:
                raise ConditionalWriteConflict("worker attempt or event limit is stale") from error
            self._raise_provider("append stage event", error)

    def complete_run(
        self,
        run_id: str,
        attempt_id: str,
        status: RunStatus,
        report_ref: ArtifactRef,
        summary: Mapping[str, Any],
    ) -> None:
        if status not in TERMINAL_STATUSES:
            raise ValueError("completion status must be terminal")
        self.get_report(report_ref)
        stage = "failed" if status == "failed" else "ready"
        try:
            self._dynamodb.update_item(
                TableName=self._table_name,
                Key=self._run_key(run_id),
                UpdateExpression=(
                    "SET #status = :terminal, stage = :stage, stage_order = :stage_order, "
                    "report_key = :report_key, report_sha256 = :report_sha256, "
                    "report_id = :report_id, summary_json = :summary"
                ),
                ConditionExpression="#status = :running AND attempt_id = :attempt",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":running": _s("running"),
                    ":attempt": _s(attempt_id),
                    ":terminal": _s(status),
                    ":stage": _s(stage),
                    ":stage_order": _n(5),
                    ":report_key": _s(report_ref.key),
                    ":report_sha256": _s(report_ref.sha256),
                    ":report_id": _s(report_ref.artifact_id),
                    ":summary": _s(_json(summary)),
                },
            )
            return
        except Exception as error:
            if _error_code(error) not in _CONDITIONAL_CODES:
                self._raise_provider("complete run", error)
        run = self.get_run(run_id)
        if (
            run is not None
            and run.status == status
            and run.attempt_id == attempt_id
            and run.report_ref == report_ref
            and run.summary == dict(summary)
        ):
            return
        raise ConditionalWriteConflict("run completion is stale or contradictory")

    def _put_artifact(
        self,
        artifact_id: str,
        prefix: str,
        value: Mapping[str, Any],
        validator: ArtifactValidator,
    ) -> ArtifactRef:
        UUID(artifact_id)
        payload = canonical_json_bytes(validator(value))
        key = f"{prefix}/{artifact_id}.json"
        reference = ArtifactRef(key, sha256_hex(payload), artifact_id)
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
            raise ImmutableArtifactConflict("artifact key already stores different bytes")
        return reference

    def _get_artifact(
        self,
        reference: ArtifactRef,
        prefix: str,
        validator: ArtifactValidator,
    ) -> dict[str, Any]:
        UUID(reference.artifact_id)
        if reference.key != f"{prefix}/{reference.artifact_id}.json":
            raise ValueError("artifact reference key is not canonical")
        try:
            response = self._s3.get_object(Bucket=self._bucket_name, Key=reference.key)
            body = response["Body"]
            payload = body.read() if hasattr(body, "read") else bytes(body)
        except Exception as error:
            if _error_code(error) in _NOT_FOUND_CODES:
                raise ArtifactNotFound("artifact does not exist") from error
            self._raise_provider("read artifact", error)
        return deserialize_verified(payload, reference.sha256, validator)

    def put_snapshot(self, snapshot: Mapping[str, Any]) -> ArtifactRef:
        return self._put_artifact(
            str(snapshot["snapshot_id"]), "snapshots", snapshot, self._snapshot_validator
        )

    def get_snapshot(self, snapshot_ref: ArtifactRef) -> dict[str, Any]:
        return self._get_artifact(snapshot_ref, "snapshots", self._snapshot_validator)

    def attach_snapshot(self, run_id: str, snapshot_ref: ArtifactRef) -> RunRecord:
        """Attach a verified snapshot reference without allowing replacement."""

        self.get_snapshot(snapshot_ref)
        reserved_run = self.get_run(run_id)
        if reserved_run is None:
            raise RunNotFound("run does not exist")
        expected_snapshot_id = reserved_run.metadata.get("snapshot_id")
        if (
            expected_snapshot_id is not None
            and expected_snapshot_id != snapshot_ref.artifact_id
        ):
            raise ConditionalWriteConflict(
                "snapshot does not match the run's reserved snapshot_id"
            )
        try:
            response = self._dynamodb.update_item(
                TableName=self._table_name,
                Key=self._run_key(run_id),
                UpdateExpression=(
                    "SET snapshot_key = :key, snapshot_sha256 = :sha256, "
                    "snapshot_id = :snapshot_id"
                ),
                ConditionExpression=(
                    "#status = :pending AND "
                    "(attribute_not_exists(snapshot_key) OR "
                    "(snapshot_key = :key AND snapshot_sha256 = :sha256 "
                    "AND snapshot_id = :snapshot_id))"
                ),
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":pending": _s("pending_enqueue"),
                    ":key": _s(snapshot_ref.key),
                    ":sha256": _s(snapshot_ref.sha256),
                    ":snapshot_id": _s(snapshot_ref.artifact_id),
                },
                ReturnValues="ALL_NEW",
            )
            attributes = response.get("Attributes")
            if not isinstance(attributes, Mapping):
                run = self.get_run(run_id)
                if run is None:
                    raise RunNotFound("run does not exist")
                if run.snapshot_ref != snapshot_ref:
                    raise StorageUnavailable(
                        "snapshot attachment returned no persisted reference"
                    )
                return run
            return self._decode_run(attributes)
        except (ArtifactNotFound, RunNotFound):
            raise
        except Exception as error:
            if _error_code(error) not in _CONDITIONAL_CODES:
                self._raise_provider("attach snapshot", error)
        run = self.get_run(run_id)
        if run is not None and run.snapshot_ref == snapshot_ref:
            return run
        raise ConditionalWriteConflict("snapshot attachment is stale or contradictory")

    def put_report(self, report: Mapping[str, Any]) -> ArtifactRef:
        return self._put_artifact(
            str(report["report_id"]), "reports", report, self._report_validator
        )

    def get_report(self, report_ref: ArtifactRef) -> dict[str, Any]:
        return self._get_artifact(report_ref, "reports", self._report_validator)

    def create_report_download_url(
        self, report_ref: ArtifactRef, expires_seconds: int = 300
    ) -> str:
        expected_key = f"reports/{report_ref.artifact_id}.json"
        if report_ref.key != expected_key or not 1 <= expires_seconds <= 300:
            raise ValueError("report reference or expiry is invalid")
        try:
            return self._s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket_name, "Key": expected_key},
                ExpiresIn=expires_seconds,
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
                raise DailyLimitExceeded("UTC daily run limit reached") from error
            self._raise_provider("claim daily run capacity", error)

    def claim_active_run(
        self,
        owner_sub: str,
        run_id: str,
        now: datetime,
        lease_seconds: int,
    ) -> ActiveRunLease:
        expires_at = now + timedelta(seconds=lease_seconds)
        key = self._active_key(owner_sub)
        item = {
            **key,
            "owner_sub": _s(owner_sub),
            "run_id": _s(run_id),
            "expires_at": _n(int(expires_at.timestamp())),
        }
        try:
            self._dynamodb.put_item(
                TableName=self._table_name,
                Item=item,
                ConditionExpression=(
                    "attribute_not_exists(PK) OR expires_at <= :now OR run_id = :run_id"
                ),
                ExpressionAttributeValues={
                    ":now": _n(int(now.timestamp())),
                    ":run_id": _s(run_id),
                },
            )
            return ActiveRunLease(owner_sub, run_id, expires_at)
        except Exception as error:
            if _error_code(error) in _CONDITIONAL_CODES:
                raise ActiveRunConflict("operator already has an active run") from error
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
        decision: str,
        note: str,
    ) -> ReviewRecord:
        if len(note) > 1_000:
            raise ValueError("review note exceeds 1000 characters")
        if decision not in {"acknowledged", "request_more_evidence"}:
            raise ValueError("review decision is invalid")
        review = ReviewRecord(
            review_id=self._id_factory(),
            run_id=run_id,
            actor_sub=actor_sub,
            report_id=report_id,
            decision=decision,
            note=note,
        )
        review_item = {
            "PK": _s(f"RUN#{run_id}"),
            "SK": _s(f"REVIEW#{review.review_id}"),
            "review_id": _s(review.review_id),
            "run_id": _s(run_id),
            "actor_sub": _s(actor_sub),
            "report_id": _s(report_id),
            "decision": _s(decision),
            "note": _s(note),
        }
        try:
            self._dynamodb.transact_write_items(
                TransactItems=[
                    {
                        "ConditionCheck": {
                            "TableName": self._table_name,
                            "Key": self._run_key(run_id),
                            "ConditionExpression": "report_id = :report_id",
                            "ExpressionAttributeValues": {":report_id": _s(report_id)},
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table_name,
                            "Item": review_item,
                            "ConditionExpression": "attribute_not_exists(SK)",
                        }
                    },
                ]
            )
            return review
        except Exception as error:
            if _error_code(error) in _CONDITIONAL_CODES:
                raise ReviewConflict("review does not reference the current report") from error
            self._raise_provider("save review", error)

    def set_public_demo(self, run_id: str, summary: Mapping[str, Any]) -> None:
        index_item = {
            **self._public_key(run_id),
            "run_id": _s(run_id),
            "summary_json": _s(_json(summary)),
        }
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
                            "Item": index_item,
                            "ConditionExpression": "attribute_not_exists(SK)",
                        }
                    },
                ]
            )
        except Exception as error:
            if _error_code(error) in _CONDITIONAL_CODES:
                raise ConditionalWriteConflict("public demo index update conflicted") from error
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
            RunSummary(
                _get_s(item, "run_id", "") or "",
                json.loads(_get_s(item, "summary_json", "{}") or "{}"),
            )
            for item in response.get("Items", [])
        ]

    def get_public_run(self, run_id: str) -> RunSummary | None:
        try:
            response = self._dynamodb.get_item(
                TableName=self._table_name,
                Key=self._public_key(run_id),
                ConsistentRead=True,
            )
        except Exception as error:
            self._raise_provider("read public run", error)
        item = response.get("Item")
        if not isinstance(item, Mapping):
            return None
        return RunSummary(
            run_id,
            json.loads(_get_s(item, "summary_json", "{}") or "{}"),
        )
