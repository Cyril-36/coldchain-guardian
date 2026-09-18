import json

import pytest

from coldchain.api.queue import SqsQueueSender
from coldchain.storage import TemporaryEnqueueError


class SqsFake:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def send_message(self, **kwargs):
        if self.error:
            raise self.error
        self.calls.append(kwargs)
        return {"MessageId": "message-1"}


def test_sqs_sender_emits_only_canonical_worker_fields() -> None:
    client = SqsFake()
    sender = SqsQueueSender("https://sqs.example/queue", sqs_client=client)

    sender.send_run(
        "3687c2e2-2275-4f2a-9f5b-86bf079a950f",
        "b3df257a-65f1-491f-b3e0-d1419ad093c6",
    )

    body = json.loads(client.calls[0]["MessageBody"])
    assert body == {
        "run_id": "3687c2e2-2275-4f2a-9f5b-86bf079a950f",
        "snapshot_id": "b3df257a-65f1-491f-b3e0-d1419ad093c6",
        "schema_version": "1.0",
    }


def test_sqs_sender_maps_provider_error_to_retryable_error() -> None:
    sender = SqsQueueSender("https://sqs.example/queue", sqs_client=SqsFake(RuntimeError()))

    with pytest.raises(TemporaryEnqueueError) as caught:
        sender.send_run(
            "3687c2e2-2275-4f2a-9f5b-86bf079a950f",
            "b3df257a-65f1-491f-b3e0-d1419ad093c6",
        )

    assert caught.value.retryable is True
