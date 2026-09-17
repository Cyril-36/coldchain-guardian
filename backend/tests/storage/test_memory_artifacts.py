from __future__ import annotations

from copy import deepcopy

import pytest

from coldchain.storage import (
    ArtifactDigestMismatch,
    ArtifactRef,
    ConditionalWriteConflict,
    ImmutableArtifactConflict,
)
from coldchain.storage.memory import MemoryStorage

from .conftest import stable_uuid


def test_snapshot_round_trip_is_immutable(memory: MemoryStorage, snapshot: dict) -> None:
    reference = memory.put_snapshot(snapshot)
    loaded = memory.get_snapshot(reference)
    loaded["policy"]["max_c"] = 99
    assert memory.get_snapshot(reference)["policy"]["max_c"] == 8.0


def test_snapshot_reference_attaches_to_run_once(
    memory: MemoryStorage, snapshot: dict
) -> None:
    run = memory.create_or_get_run("operator", "key", "hash", {})
    snapshot_ref = memory.put_snapshot(snapshot)
    attached = memory.attach_snapshot(run.run_id, snapshot_ref)
    assert attached.snapshot_ref == snapshot_ref
    assert memory.attach_snapshot(run.run_id, snapshot_ref).snapshot_ref == snapshot_ref
    loaded_run = memory.get_run(run.run_id)
    assert loaded_run is not None and loaded_run.snapshot_ref is not None
    assert memory.get_snapshot(loaded_run.snapshot_ref) == memory.get_snapshot(snapshot_ref)
    assert memory.mark_queued(run.run_id)


def test_snapshot_attachment_rejects_replacement(
    memory: MemoryStorage, snapshot: dict
) -> None:
    run = memory.create_or_get_run("operator", "key", "hash", {})
    first_ref = memory.put_snapshot(snapshot)
    memory.attach_snapshot(run.run_id, first_ref)
    changed = deepcopy(snapshot)
    changed["snapshot_id"] = stable_uuid("replacement-snapshot")
    second_ref = memory.put_snapshot(changed)
    with pytest.raises(ConditionalWriteConflict):
        memory.attach_snapshot(run.run_id, second_ref)


def test_report_round_trip_is_immutable(memory: MemoryStorage, report: dict) -> None:
    reference = memory.put_report(report)
    loaded = memory.get_report(reference)
    loaded["outcome"] = "changed"
    assert memory.get_report(reference)["outcome"] == "no_excursion"


def test_digest_is_verified_on_read(memory: MemoryStorage, report: dict) -> None:
    reference = memory.put_report(report)
    wrong_reference = ArtifactRef(reference.key, "0" * 64, reference.artifact_id)
    with pytest.raises(ArtifactDigestMismatch):
        memory.get_report(wrong_reference)


def test_changed_stored_bytes_are_rejected(memory: MemoryStorage, report: dict) -> None:
    reference = memory.put_report(report)
    memory._corrupt_artifact_for_test(reference.key, b"{}")
    with pytest.raises(ArtifactDigestMismatch):
        memory.get_report(reference)


def test_conflicting_second_write_is_rejected(memory: MemoryStorage, report: dict) -> None:
    memory.put_report(report)
    changed = deepcopy(report)
    changed["outcome"] = "unresolved"
    with pytest.raises(ImmutableArtifactConflict):
        memory.put_report(changed)


def test_artifact_keys_are_opaque(memory: MemoryStorage, snapshot: dict, report: dict) -> None:
    snapshot_ref = memory.put_snapshot(snapshot)
    report_ref = memory.put_report(report)
    assert snapshot_ref.key == f"snapshots/{snapshot['snapshot_id']}.json"
    assert report_ref.key == f"reports/{report['report_id']}.json"
    combined = snapshot_ref.key + report_ref.key
    assert "normal" not in combined
    assert "scenario" not in combined


def test_identical_second_write_returns_same_reference(memory: MemoryStorage, report: dict) -> None:
    assert memory.put_report(report) == memory.put_report(report)


def test_forged_artifact_path_is_rejected(memory: MemoryStorage, report: dict) -> None:
    reference = memory.put_report(report)
    forged = ArtifactRef("snapshots/private.json", reference.sha256, reference.artifact_id)
    with pytest.raises(ValueError, match="canonical"):
        memory.get_report(forged)
