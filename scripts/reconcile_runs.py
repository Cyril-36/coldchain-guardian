#!/usr/bin/env python3
"""Reconcile runs abandoned after SQS exhausted its retries.

docs/VERIFICATION.md section 2 requires DLQ and stale-job reconciliation to be
demonstrated, with retry limits respected. A message that exceeds maxReceiveCount stops
being redelivered, leaving its run `running` with an attempt lease and no report.
Nothing else recovers it.

This reads run IDs from the dead-letter queue (or takes them directly), marks each
abandoned run `failed` with a visible reason, and releases the operator's active-run
slot so they are not locked out.

Dry run by default. Pass --apply to make changes.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from coldchain.contracts import QueueMessage  # noqa: E402
from coldchain.storage import AwsStorage  # noqa: E402
from coldchain.worker.reconcile import reconcile_runs  # noqa: E402


def run_ids_from_dlq(queue_url: str, region: str, max_messages: int, delete: bool) -> list[str]:
    """Read run IDs from the dead-letter queue.

    Messages are left on the queue unless --apply is given, so a dry run cannot lose
    the evidence of what failed.
    """
    import boto3

    sqs = boto3.client("sqs", region_name=region)
    found: list[str] = []
    while len(found) < max_messages:
        batch = sqs.receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=min(10, max_messages - len(found)),
            WaitTimeSeconds=1,
            VisibilityTimeout=30,
        ).get("Messages", [])
        if not batch:
            break
        for message in batch:
            try:
                found.append(QueueMessage.model_validate(json.loads(message["Body"])).run_id)
            except Exception:  # noqa: BLE001 - a malformed body is reported, not fatal
                print(f"  skipped unparseable message {message['MessageId']}", file=sys.stderr)
                continue
            if delete:
                sqs.delete_message(
                    QueueUrl=queue_url, ReceiptHandle=message["ReceiptHandle"]
                )
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", required=True, help="RUNS_TABLE")
    parser.add_argument("--bucket", required=True, help="ARTIFACT_BUCKET")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--dlq-url", default=None, help="dead-letter queue to drain")
    parser.add_argument("--run-id", action="append", default=[], help="reconcile these directly")
    parser.add_argument("--max-messages", type=int, default=50)
    parser.add_argument("--apply", action="store_true", help="make changes (default: dry run)")
    args = parser.parse_args()

    run_ids = list(args.run_id)
    if args.dlq_url:
        run_ids += run_ids_from_dlq(
            args.dlq_url, args.region, args.max_messages, delete=args.apply
        )
    if not run_ids:
        print(json.dumps({"scanned": 0, "note": "no run IDs supplied or found on the DLQ"}))
        return 0

    storage = AwsStorage(table_name=args.table, bucket_name=args.bucket)
    result = reconcile_runs(storage, run_ids, dry_run=not args.apply)

    print(
        json.dumps(
            {"dry_run": not args.apply, "scanned": len(set(run_ids)), **asdict(result)}, indent=2
        )
    )
    if not args.apply and result.failed:
        print(
            f"\n{len(result.failed)} run(s) would be failed. Re-run with --apply.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
