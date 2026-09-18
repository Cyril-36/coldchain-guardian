"""Production Lambda entry point."""

from __future__ import annotations

import os
from typing import Any

from coldchain.storage import AwsStorage

from .application import ApiApplication
from .queue import SqsQueueSender
from .service import ApiService

_application: ApiApplication | None = None


def build_application_from_env() -> ApiApplication:
    bucket = os.environ["ARTIFACT_BUCKET"]
    table = os.environ["RUNS_TABLE"]
    queue_url = os.environ["INVESTIGATION_QUEUE_URL"]
    allowed = frozenset(
        item.strip() for item in os.environ["ALLOWED_OPERATOR_SUBS"].split(",") if item.strip()
    )
    if not allowed:
        raise RuntimeError("ALLOWED_OPERATOR_SUBS must contain at least one operator subject")
    storage = AwsStorage(bucket_name=bucket, table_name=table)
    service = ApiService(storage, SqsQueueSender(queue_url))
    return ApiApplication(
        service,
        build_sha=os.environ.get("BUILD_SHA", "unknown"),
        allowed_operator_subs=allowed,
    )


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    global _application
    if _application is None:
        _application = build_application_from_env()
    return _application.handle(event)
