from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from itertools import count
from typing import Any
from uuid import UUID, uuid5

import pytest

from coldchain.api import ApiApplication, ApiService
from coldchain.storage import MemoryStorage, TemporaryEnqueueError

NOW = datetime(2026, 9, 18, 9, 0, tzinfo=UTC)
NAMESPACE = UUID("e169fe6d-4565-4e8f-bc88-a10ea139e9fa")


class FakeQueue:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []
        self.failures_remaining = 0

    def send_run(self, run_id: str, snapshot_id: str, schema_version: str = "1.0") -> None:
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise TemporaryEnqueueError("queue unavailable")
        self.messages.append((run_id, snapshot_id, schema_version))

    def enqueue_run(self, run_id: str, snapshot_id: str, schema_version: str = "1.0") -> None:
        self.send_run(run_id, snapshot_id, schema_version)


@pytest.fixture
def api_factory() -> Callable[..., tuple[ApiApplication, MemoryStorage, FakeQueue]]:
    def factory(*, queue: FakeQueue | None = None, storage: MemoryStorage | None = None):
        ids = count()
        storage = storage or MemoryStorage(
            id_factory=lambda: str(uuid5(NAMESPACE, f"id-{next(ids)}")),
            clock=lambda: NOW,
        )
        queue = queue or FakeQueue()
        service = ApiService(storage, queue, clock=lambda: NOW, seed_factory=lambda: 917)
        return (
            ApiApplication(
                service,
                build_sha="abc123",
                allowed_operator_subs=frozenset({"operator-a", "operator-b"}),
            ),
            storage,
            queue,
        )

    return factory


def event(
    method: str,
    path: str,
    *,
    body: Any = None,
    subject: str | None = "operator-a",
    scope: str = "coldchain/write",
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    claims = {} if subject is None else {"sub": subject, "scope": scope}
    result: dict[str, Any] = {
        "version": "2.0",
        "rawPath": path,
        "headers": headers or {},
        "requestContext": {
            "requestId": "request-123",
            "http": {"method": method},
            "authorizer": {"jwt": {"claims": claims}},
        },
        "isBase64Encoded": False,
    }
    if body is not None:
        result["body"] = json.dumps(body)
    return result


def decode(response: dict[str, Any]) -> Any:
    return json.loads(response["body"])
