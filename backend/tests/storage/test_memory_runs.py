from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from threading import Barrier

import pytest

from coldchain.storage import (
    ActiveRunConflict,
    ConditionalWriteConflict,
    DailyLimitExceeded,
    IdempotencyConflict,
    LeaseConflict,
)
from coldchain.storage.memory import MemoryStorage

from .conftest import stable_uuid

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def _create_run(
    storage: MemoryStorage,
    *,
    owner: str = "operator-a",
    key: str = "request-key",
    request_hash: str = "body-hash",
):
    return storage.create_or_get_run(owner, key, request_hash, {"source": "test"})


def test_create_and_get_run_returns_defensive_copies(memory: MemoryStorage) -> None:
    created = _create_run(memory)
    created.metadata["changed"] = True
    loaded = memory.get_run(created.run_id)
    assert loaded is not None
    assert loaded.metadata == {"source": "test"}
    assert loaded.status == "pending_enqueue"


def test_same_idempotent_request_returns_same_run(memory: MemoryStorage) -> None:
    first = _create_run(memory)
    second = _create_run(memory)
    assert second.run_id == first.run_id


def test_changed_body_for_same_key_conflicts(memory: MemoryStorage) -> None:
    _create_run(memory)
    with pytest.raises(IdempotencyConflict):
        _create_run(memory, request_hash="different-hash")


def test_same_key_is_scoped_by_operator(memory: MemoryStorage) -> None:
    first = _create_run(memory, owner="operator-a")
    second = _create_run(memory, owner="operator-b")
    assert first.run_id != second.run_id


def test_idempotency_mapping_can_be_replaced_after_24_hours() -> None:
    current = [NOW]
    identifiers = iter([stable_uuid("first-run"), stable_uuid("second-run")])
    storage = MemoryStorage(
        id_factory=lambda: next(identifiers),
        clock=lambda: current[0],
    )
    first = _create_run(storage)
    current[0] += timedelta(hours=24, seconds=1)
    second = _create_run(storage, request_hash="new-body")
    assert first.run_id != second.run_id


def test_first_worker_claims_and_same_attempt_renews(memory: MemoryStorage) -> None:
    run = _create_run(memory)
    first = memory.claim_run(run.run_id, "attempt-a", NOW, 90)
    renewed = memory.claim_run(run.run_id, "attempt-a", NOW + timedelta(seconds=5), 90)
    assert first.claimed and renewed.claimed
    assert renewed.reason == "same_attempt"
    assert renewed.run.lease_expires_at == NOW + timedelta(seconds=95)


def test_competing_active_worker_is_rejected(memory: MemoryStorage) -> None:
    run = _create_run(memory)
    memory.claim_run(run.run_id, "attempt-a", NOW, 90)
    with pytest.raises(LeaseConflict):
        memory.claim_run(run.run_id, "attempt-b", NOW + timedelta(seconds=1), 90)


def test_expired_worker_lease_can_be_replaced(memory: MemoryStorage) -> None:
    run = _create_run(memory)
    memory.claim_run(run.run_id, "attempt-a", NOW, 30)
    result = memory.claim_run(run.run_id, "attempt-b", NOW + timedelta(seconds=31), 30)
    assert result.claimed
    assert result.run.attempt_id == "attempt-b"


def test_stale_attempt_cannot_set_stage_or_complete(memory: MemoryStorage, report: dict) -> None:
    run = _create_run(memory)
    memory.claim_run(run.run_id, "attempt-a", NOW, 10)
    memory.claim_run(run.run_id, "attempt-b", NOW + timedelta(seconds=11), 90)
    report_ref = memory.put_report(report)
    with pytest.raises(ConditionalWriteConflict):
        memory.set_stage(run.run_id, "attempt-a", "detecting")
    with pytest.raises(ConditionalWriteConflict):
        memory.complete_run(run.run_id, "attempt-a", "completed", report_ref, {"ok": True})


def test_stage_regression_is_rejected(memory: MemoryStorage) -> None:
    run = _create_run(memory)
    memory.claim_run(run.run_id, "attempt", NOW, 90)
    memory.set_stage(run.run_id, "attempt", "collecting_evidence")
    with pytest.raises(ConditionalWriteConflict):
        memory.set_stage(run.run_id, "attempt", "detecting")


def test_stage_events_are_copied_and_bounded(memory: MemoryStorage) -> None:
    run = _create_run(memory)
    memory.claim_run(run.run_id, "attempt", NOW, 90)
    event = {"event_id": "one", "stage": "detecting"}
    memory.append_stage_event(run.run_id, "attempt", event)
    event["stage"] = "changed"
    loaded = memory.get_run(run.run_id)
    assert loaded is not None
    assert loaded.stage_events == [{"event_id": "one", "stage": "detecting"}]
    for index in range(39):
        memory.append_stage_event(run.run_id, "attempt", {"event_id": str(index)})
    with pytest.raises(ConditionalWriteConflict):
        memory.append_stage_event(run.run_id, "attempt", {"event_id": "overflow"})


def test_api_queued_update_cannot_regress_running(memory: MemoryStorage) -> None:
    run = _create_run(memory)
    memory.claim_run(run.run_id, "attempt", NOW, 90)
    assert memory.mark_queued(run.run_id) is False
    assert memory.get_run(run.run_id).status == "running"  # type: ignore[union-attr]


def test_api_queued_update_cannot_regress_terminal(memory: MemoryStorage, report: dict) -> None:
    run = _create_run(memory)
    memory.claim_run(run.run_id, "attempt", NOW, 90)
    report_ref = memory.put_report(report)
    memory.complete_run(run.run_id, "attempt", "completed", report_ref, {"ok": True})
    assert memory.mark_queued(run.run_id) is False
    assert memory.get_run(run.run_id).status == "completed"  # type: ignore[union-attr]


def test_duplicate_completion_is_idempotent_but_conflict_is_rejected(
    memory: MemoryStorage, report: dict
) -> None:
    run = _create_run(memory)
    memory.claim_run(run.run_id, "attempt", NOW, 90)
    report_ref = memory.put_report(report)
    memory.complete_run(run.run_id, "attempt", "completed", report_ref, {"ok": True})
    memory.complete_run(run.run_id, "attempt", "completed", report_ref, {"ok": True})
    with pytest.raises(ConditionalWriteConflict):
        memory.complete_run(run.run_id, "attempt", "needs_review", report_ref, {"ok": True})


def test_daily_cap_boundary(memory: MemoryStorage) -> None:
    utc_day = date(2026, 9, 17)
    assert [memory.claim_daily_run(utc_day, 3) for _ in range(3)] == [1, 2, 3]
    with pytest.raises(DailyLimitExceeded):
        memory.claim_daily_run(utc_day, 3)


def test_concurrent_daily_final_slot_has_one_winner(memory: MemoryStorage) -> None:
    utc_day = date(2026, 9, 17)
    memory.claim_daily_run(utc_day, 2)
    barrier = Barrier(2)

    def claim() -> str:
        barrier.wait()
        try:
            memory.claim_daily_run(utc_day, 2)
            return "claimed"
        except DailyLimitExceeded:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: claim(), range(2)))
    assert sorted(results) == ["claimed", "rejected"]


def test_active_run_is_single_and_expiry_allows_replacement(memory: MemoryStorage) -> None:
    memory.claim_active_run("operator", "run-a", NOW, 60)
    with pytest.raises(ActiveRunConflict):
        memory.claim_active_run("operator", "run-b", NOW + timedelta(seconds=1), 60)
    replacement = memory.claim_active_run("operator", "run-b", NOW + timedelta(seconds=61), 60)
    assert replacement.run_id == "run-b"


def test_concurrent_active_run_claim_has_one_winner(memory: MemoryStorage) -> None:
    barrier = Barrier(2)

    def claim(run_id: str) -> str:
        barrier.wait()
        try:
            memory.claim_active_run("operator", run_id, NOW, 60)
            return "claimed"
        except ActiveRunConflict:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ["run-a", "run-b"]))
    assert sorted(results) == ["claimed", "rejected"]
