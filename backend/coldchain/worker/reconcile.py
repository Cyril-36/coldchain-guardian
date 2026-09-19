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
from uuid import uuid4

from coldchain.contracts.enums import RunStatus
from coldchain.storage import ConditionalCheckFailedError, StorageProtocol

# Terminal states a reconciler must never overwrite.
_TERMINAL = {RunStatus.completed, RunStatus.needs_review, RunStatus.failed}

FAILURE_SUMMARY = "investigation_abandoned_after_retries"

# Long enough to complete the run we just claimed, short enough that a crash here
# does not strand it again for long.
_RECONCILE_LEASE_SECONDS = 60


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

    A run is only reconciled when its lease has expired, or when it never had one. An
    unexpired lease means a worker may still be alive and holding it, and failing the
    run underneath that worker would race its own completion.

    A run still `queued` with no attempt is reconcilable too: its message reached the
    dead-letter queue before any worker claimed it, so nothing will ever pick it up.
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
        if dry_run:
            result.failed.append(run_id)
            continue

        attempt_id = run.attempt_id
        if attempt_id is None:
            # Queued but never claimed -- the message reached the DLQ before any
            # worker took it, so there is no attempt to complete against. Claiming it
            # ourselves creates one. The claim is conditional, so a worker that takes
            # the run first simply wins and we report a conflict.
            attempt_id = str(uuid4())
            claim = storage.claim_run(run_id, attempt_id, moment, _RECONCILE_LEASE_SECONDS)
            if not claim.success:
                result.conflicted.append(run_id)
                continue

        try:
            storage.complete_run(run_id, attempt_id, RunStatus.failed, None, FAILURE_SUMMARY)
        except ConditionalCheckFailedError:
            # A worker completed it between our read and our write. Its result wins.
            result.conflicted.append(run_id)
            continue
        result.failed.append(run_id)

    return result
