from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import pytest

from coldchain.contracts.schemas import Report, Snapshot
from coldchain.simulator import generate_snapshot
from coldchain.storage import MemoryStorage

TEST_NAMESPACE = UUID("09b35277-9d02-470b-9f42-5aa7b37a0c93")
BASE_TIME = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def stable_uuid(name: str) -> str:
    return str(uuid5(TEST_NAMESPACE, name))


def run_metadata(**overrides: Any) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "scenario_id": "normal_control",
        "seed": 17,
        "base_timestamp": BASE_TIME,
    }
    metadata.update(overrides)
    return metadata


@pytest.fixture
def snapshot() -> Snapshot:
    return Snapshot.model_validate(
        generate_snapshot(
            "normal_control",
            17,
            BASE_TIME,
            snapshot_id=stable_uuid("snapshot"),
            shipment_id=stable_uuid("shipment"),
        )
    )


@pytest.fixture
def report() -> Report:
    path = Path(__file__).parents[3] / "contracts" / "examples" / "normal-report.json"
    return Report.model_validate(json.loads(path.read_text(encoding="utf-8")))


@pytest.fixture
def memory() -> MemoryStorage:
    counter = iter(range(200))
    return MemoryStorage(
        id_factory=lambda: stable_uuid(f"generated-{next(counter)}"),
        clock=lambda: NOW,
    )


class FakeAwsError(Exception):
    def __init__(
        self, code: str, cancellation_reasons: list[dict[str, str]] | None = None
    ) -> None:
        self.response: dict[str, Any] = {
            "Error": {"Code": code, "Message": "provider detail"}
        }
        if cancellation_reasons is not None:
            self.response["CancellationReasons"] = cancellation_reasons
        super().__init__("provider detail must not escape")


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.corrupt_reads: dict[str, bytes] = {}
        self.fail_with: Exception | None = None

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("put_object", kwargs))
        if self.fail_with is not None:
            raise self.fail_with
        key = kwargs["Key"]
        if key in self.objects:
            raise FakeAwsError("PreconditionFailed")
        self.objects[key] = bytes(kwargs["Body"])
        return {}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_object", kwargs))
        if self.fail_with is not None:
            raise self.fail_with
        key = kwargs["Key"]
        if key not in self.objects:
            raise FakeAwsError("NoSuchKey")
        payload = self.corrupt_reads.get(key, self.objects[key])
        return {"Body": BytesIO(payload)}

    def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        self.calls.append(("generate_presigned_url", {"operation": operation, **kwargs}))
        return f"https://example.invalid/{kwargs['Params']['Key']}"


class RecordingDynamo:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[str, list[dict[str, Any]]] = {}
        self.errors: dict[str, list[Exception]] = {}

    def queue_response(self, method: str, response: dict[str, Any]) -> None:
        self.responses.setdefault(method, []).append(response)

    def queue_error(self, method: str, error: Exception) -> None:
        self.errors.setdefault(method, []).append(error)

    def _call(self, method: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, kwargs))
        if errors := self.errors.get(method):
            raise errors.pop(0)
        if responses := self.responses.get(method):
            return responses.pop(0)
        return {}

    def transact_write_items(self, **kwargs: Any) -> dict[str, Any]:
        return self._call("transact_write_items", kwargs)

    def get_item(self, **kwargs: Any) -> dict[str, Any]:
        return self._call("get_item", kwargs)

    def update_item(self, **kwargs: Any) -> dict[str, Any]:
        return self._call("update_item", kwargs)

    def put_item(self, **kwargs: Any) -> dict[str, Any]:
        return self._call("put_item", kwargs)

    def delete_item(self, **kwargs: Any) -> dict[str, Any]:
        return self._call("delete_item", kwargs)

    def query(self, **kwargs: Any) -> dict[str, Any]:
        return self._call("query", kwargs)
