from __future__ import annotations

import pytest

from coldchain.storage import ReviewConflict
from coldchain.storage.memory import MemoryStorage

from .test_memory_runs import NOW, _create_run


def _completed_run(memory: MemoryStorage, report: dict):
    run = _create_run(memory)
    memory.claim_run(run.run_id, "attempt", NOW, 90)
    report_ref = memory.put_report(report)
    memory.complete_run(run.run_id, "attempt", "completed", report_ref, {"result": "done"})
    return run, report_ref


def test_reviews_are_append_only_and_preserve_actor(memory: MemoryStorage, report: dict) -> None:
    run, report_ref = _completed_run(memory, report)
    first = memory.save_review(
        run.run_id, "actor-a", report_ref.artifact_id, "acknowledged", "checked"
    )
    second = memory.save_review(
        run.run_id,
        "actor-b",
        report_ref.artifact_id,
        "request_more_evidence",
        "need logs",
    )
    reviews = memory.list_reviews(run.run_id)
    assert reviews == [first, second]
    assert [review.actor_sub for review in reviews] == ["actor-a", "actor-b"]


def test_wrong_report_review_is_rejected(memory: MemoryStorage, report: dict) -> None:
    run, _ = _completed_run(memory, report)
    with pytest.raises(ReviewConflict):
        memory.save_review(run.run_id, "actor", "wrong-report", "acknowledged", "")


def test_review_note_limit_is_enforced(memory: MemoryStorage, report: dict) -> None:
    run, report_ref = _completed_run(memory, report)
    with pytest.raises(ValueError, match="1000"):
        memory.save_review(
            run.run_id,
            "actor",
            report_ref.artifact_id,
            "acknowledged",
            "x" * 1001,
        )


def test_public_listing_only_contains_explicitly_curated_runs(
    memory: MemoryStorage,
) -> None:
    public = _create_run(memory, key="public")
    private = _create_run(memory, key="private")
    memory.set_public_demo(public.run_id, {"label": "curated"})
    listed = memory.list_public_runs()
    assert [item.run_id for item in listed] == [public.run_id]
    assert memory.get_public_run(private.run_id) is None


def test_existing_private_uuid_does_not_grant_public_access(memory: MemoryStorage) -> None:
    private = _create_run(memory)
    assert memory.get_run(private.run_id) is not None
    assert memory.get_public_run(private.run_id) is None
