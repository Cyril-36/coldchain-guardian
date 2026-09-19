"""Reconcile runs whose investigation never reached a terminal state.

A message that exhausts `maxReceiveCount` moves to the dead-letter queue. The run it
referred to is left `running`, holding an attempt lease, with no report. Nothing else
in the system moves it: SQS has stopped redelivering, the worker will never see it
again, and the dashboard polls a run that can never finish.

This module marks such a run `failed` with a visible reason and releases its lease, so
an operator sees an honest terminal state instead of an indefinite spinner.

The logic lives here rather than in the CLI so it can be tested against MemoryStorage
without AWS. `scripts/reconcile_runs.py` is the thin wrapper that finds the run IDs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from coldchain.contracts.enums import RunStatus
from coldchain.storage import ConditionalCheckFailedError, StorageProtocol

# Terminal states a reconciler must never overwrite.
_TERMINAL = {RunStatus.completed, RunStatus.needs_review, RunStatus.failed}

FAILURE_SUMMARY = "investigation_abandoned_after_retries"


@dataclass
class ReconcileResult:
    """What reconciliation did, reported per run rather than as a total."""

    failed: list[str] = field(default_factory=list)
    already_terminal: list[str] = field(default_factory=list)
    lease_still_active: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    conflicted: list[str] = field(default_factory=list)

    @property
    def changed(self) -> int:
        return len(self.failed)


def reconcile_runs(
    storage: StorageProtocol,
    run_ids: list[str],
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> ReconcileResult:
    """Mark abandoned runs failed. Never touches a run that could still succeed.

    A run is only reconciled when it is `running` **and** its lease has expired. An
    unexpired lease means a worker may still be alive and holding it, and failing the
    run underneath that worker would race its own completion.
    """
    moment = now or datetime.now(UTC)
    result = ReconcileResult()

    for run_id in dict.fromkeys(run_ids):
        run = storage.get_run(run_id)
        if run is None:
            result.missing.append(run_id)
            continue
        if run.status in _TERMINAL:
            result.already_terminal.append(run_id)
            continue
        if run.lease_expires_at is not None and run.lease_expires_at > moment:
            result.lease_still_active.append(run_id)
            continue
        if run.attempt_id is None:
            # Queued but never claimed: no lease to release, and no attempt to
            # complete against. Leave it for the queue rather than guessing.
            result.lease_still_active.append(run_id)
            continue
        if dry_run:
            result.failed.append(run_id)
            continue
        try:
            storage.complete_run(
                run_id, run.attempt_id, RunStatus.failed, None, FAILURE_SUMMARY
            )
        except ConditionalCheckFailedError:
            # A worker completed it between our read and our write. Its result wins.
            result.conflicted.append(run_id)
            continue
        result.failed.append(run_id)

    return result
