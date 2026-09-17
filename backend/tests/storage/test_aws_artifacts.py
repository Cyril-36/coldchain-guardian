from __future__ import annotations

from copy import deepcopy

import pytest

from coldchain.storage import (
    ArtifactDigestMismatch,
    ArtifactRef,
    AwsStorage,
    ImmutableArtifactConflict,
    StorageUnavailable,
)

from .conftest import FakeAwsError, FakeS3, RecordingDynamo, stable_uuid


def _storage(s3: FakeS3 | None = None, dynamo: RecordingDynamo | None = None) -> AwsStorage:
    return AwsStorage(
        bucket_name="private-artifacts",
        table_name="runs",
        s3_client=s3 or FakeS3(),
        dynamodb_client=dynamo or RecordingDynamo(),
        id_factory=lambda: stable_uuid("aws-generated"),
    )


def test_s3_snapshot_and_report_round_trip(snapshot: dict, report: dict) -> None:
    storage = _storage()
    snapshot_ref = storage.put_snapshot(snapshot)
    report_ref = storage.put_report(report)
    assert storage.get_snapshot(snapshot_ref) == snapshot
    assert storage.get_report(report_ref) == report


def test_s3_put_is_private_opaque_and_conditional(snapshot: dict) -> None:
    s3 = FakeS3()
    storage = _storage(s3=s3)
    reference = storage.put_snapshot(snapshot)
    method, call = s3.calls[0]
    assert method == "put_object"
    assert call["Bucket"] == "private-artifacts"
    assert call["Key"] == f"snapshots/{snapshot['snapshot_id']}.json"
    assert call["IfNoneMatch"] == "*"
    assert "ACL" not in call
    assert reference.sha256


def test_s3_conflicting_immutable_write_is_rejected(report: dict) -> None:
    storage = _storage()
    storage.put_report(report)
    changed = deepcopy(report)
    changed["outcome"] = "unresolved"
    with pytest.raises(ImmutableArtifactConflict):
        storage.put_report(changed)


def test_s3_identical_retry_is_idempotent(report: dict) -> None:
    storage = _storage()
    assert storage.put_report(report) == storage.put_report(report)


def test_s3_digest_mismatch_is_rejected(report: dict) -> None:
    s3 = FakeS3()
    storage = _storage(s3=s3)
    reference = storage.put_report(report)
    s3.corrupt_reads[reference.key] = b"{}"
    with pytest.raises(ArtifactDigestMismatch):
        storage.get_report(reference)


def test_report_download_signing_only_accepts_exact_report_reference(report: dict) -> None:
    storage = _storage()
    reference = storage.put_report(report)
    url = storage.create_report_download_url(reference)
    assert reference.key in url
    arbitrary = ArtifactRef("snapshots/other.json", reference.sha256, reference.artifact_id)
    with pytest.raises(ValueError):
        storage.create_report_download_url(arbitrary)
    with pytest.raises(ValueError):
        storage.create_report_download_url(reference, expires_seconds=301)


def test_provider_failure_is_normalized_without_raw_detail(report: dict) -> None:
    s3 = FakeS3()
    s3.fail_with = FakeAwsError("ServiceUnavailable")
    storage = _storage(s3=s3)
    with pytest.raises(StorageUnavailable) as caught:
        storage.put_report(report)
    assert "provider detail" not in str(caught.value)


def test_artifact_id_must_be_uuid(report: dict) -> None:
    report["report_id"] = "scenario-name"
    with pytest.raises(ValueError):
        _storage().put_report(report)


def test_forged_artifact_path_is_rejected(report: dict) -> None:
    storage = _storage()
    reference = storage.put_report(report)
    forged = ArtifactRef("snapshots/private.json", reference.sha256, reference.artifact_id)
    with pytest.raises(ValueError, match="canonical"):
        storage.get_report(forged)
