"""Storage-local compatibility records pending Cyril's canonical contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

RunStatus = Literal[
    "pending_enqueue",
    "queued",
    "running",
    "completed",
    "needs_review",
    "failed",
]
RunStage = Literal[
    "preparing",
    "detecting",
    "collecting_evidence",
    "comparing_hypotheses",
    "verifying",
    "ready",
    "failed",
]

TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "needs_review", "failed"})


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    key: str
    sha256: str
    artifact_id: str


@dataclass(slots=True)
class RunRecord:
    run_id: str
    owner_sub: str
    request_hash: str
    metadata: dict[str, Any]
    status: RunStatus = "pending_enqueue"
    stage: RunStage = "preparing"
    attempt_id: str | None = None
    lease_expires_at: datetime | None = None
    stage_events: list[dict[str, Any]] = field(default_factory=list)
    snapshot_ref: ArtifactRef | None = None
    report_ref: ArtifactRef | None = None
    summary: dict[str, Any] | None = None
    is_public_demo: bool = False


@dataclass(frozen=True, slots=True)
class ClaimResult:
    claimed: bool
    run: RunRecord
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    review_id: str
    run_id: str
    actor_sub: str
    report_id: str
    decision: str
    note: str


@dataclass(frozen=True, slots=True)
class RunSummary:
    run_id: str
    summary: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ActiveRunLease:
    owner_sub: str
    run_id: str
    expires_at: datetime
