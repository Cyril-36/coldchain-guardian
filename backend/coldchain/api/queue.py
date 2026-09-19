"""SQS implementation of the canonical queue sender boundary."""

from __future__ import annotations

import json
from typing import Any

from coldchain.contracts import QueueMessage
from coldchain.storage import TemporaryEnqueueError


class SqsQueueSender:
    def __init__(self, queue_url: str, *, sqs_client: Any | None = None) -> None:
        if not queue_url:
            raise ValueError("queue_url is required")
        if sqs_client is None:
            import boto3

            sqs_client = boto3.client("sqs")
        self._queue_url = queue_url
        self._sqs = sqs_client

    def send_run(self, run_id: str, snapshot_id: str, schema_version: str = "1.0") -> None:
        message = QueueMessage(
            run_id=run_id,
            snapshot_id=snapshot_id,
            schema_version=schema_version,
        )
        try:
            self._sqs.send_message(
                QueueUrl=self._queue_url,
                MessageBody=json.dumps(
                    message.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
                ),
            )
        except Exception as error:
            raise TemporaryEnqueueError("SQS queue delivery failed") from error

    def enqueue_run(self, run_id: str, snapshot_id: str, schema_version: str = "1.0") -> None:
        self.send_run(run_id, snapshot_id, schema_version)
