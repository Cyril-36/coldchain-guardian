"""Pydantic v2 schemas for ColdChain Guardian contracts.

Source of truth: docs/CONTRACTS.md. Field names use snake_case.
Timestamps are ISO 8601 UTC ending in Z.  IDs are opaque UUID strings.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    Assessment,
    CoverageStatus,
    EventType,
    EvidenceKind,
    GenerationMode,
    HypothesisType,
    NextCheckCode,
    Outcome,
    PublicStage,
    ReviewDecision,
    RunStatus,
    SensorRole,
    StageEventStatus,
    VerificationStatus,
)

# ── Strict base ─────────────────────────────────────────────────────────────


class StrictBase(BaseModel):
    """Base with extra='forbid' per CONTRACTS.md: strictly reject unknown fields."""

    model_config = ConfigDict(extra="forbid")


# ── Snapshot models ─────────────────────────────────────────────────────────


class Policy(StrictBase):
    policy_id: str
    policy_version: str
    min_c: float
    max_c: float
    expected_interval_seconds: float
    max_gap_seconds: float


class Sensor(StrictBase):
    sensor_id: str
    placement: str
    role: SensorRole


class Reading(StrictBase):
    event_id: str
    sensor_id: str
    observed_at: datetime
    temperature_c: float

    @field_validator("temperature_c")
    @classmethod
    def reject_nan_inf(cls, v: float) -> float:
        if v != v or v == float("inf") or v == float("-inf"):
            raise ValueError("NaN/infinity not allowed for temperature_c")
        return v


class Event(StrictBase):
    event_id: str
    observed_at: datetime
    event_type: EventType
    value: str
    source: str


class Snapshot(StrictBase):
    snapshot_id: str
    shipment_id: str
    schema_version: Literal["1.0"] = "1.0"
    source: Literal["simulated"] = "simulated"
    cutoff_at: datetime
    policy: Policy
    sensors: list[Sensor]
    readings: list[Reading]
    events: list[Event]

    @model_validator(mode="after")
    def validate_snapshot(self) -> Snapshot:
        # Exactly one reference sensor
        ref_count = sum(1 for s in self.sensors if s.role == SensorRole.reference)
        if ref_count != 1:
            raise ValueError(f"Exactly one reference sensor required, got {ref_count}")
        # Size limits per CONTRACTS.md
        if len(self.readings) > 2000:
            raise ValueError(f"At most 2000 readings allowed, got {len(self.readings)}")
        if len(self.events) > 200:
            raise ValueError(f"At most 200 events allowed, got {len(self.events)}")
        # All sensor_ids in readings must reference a known sensor
        known_ids = {s.sensor_id for s in self.sensors}
        for r in self.readings:
            if r.sensor_id not in known_ids:
                raise ValueError(f"Reading references unknown sensor_id: {r.sensor_id}")
        # Reject readings/events past cutoff
        for r in self.readings:
            if r.observed_at > self.cutoff_at:
                raise ValueError(
                    f"Reading {r.event_id} observed_at {r.observed_at} is past cutoff {self.cutoff_at}"
                )
        for e in self.events:
            if e.observed_at > self.cutoff_at:
                raise ValueError(
                    f"Event {e.event_id} observed_at {e.observed_at} is past cutoff {self.cutoff_at}"
                )
        return self


def snapshot_sha256(snapshot: Snapshot) -> str:
    """Canonical SHA-256 of a snapshot's JSON bytes."""
    raw = snapshot.model_dump_json(by_alias=True)
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Evidence ────────────────────────────────────────────────────────────────


class EvidenceRef(StrictBase):
    evidence_id: str
    snapshot_id: str
    kind: EvidenceKind
    record_ids: list[str]
    observed_at: datetime | None = None
    interval_start: datetime | None = None
    interval_end: datetime | None = None
    summary: str
    # Derived-metric extras
    method_version: str | None = None
    input_record_ids: list[str] | None = None


# ── Measurements ────────────────────────────────────────────────────────────


class SensorMeasurement(StrictBase):
    sensor_id: str
    role: SensorRole
    excursion_detected: bool
    first_observed_out_at: datetime | None = None
    last_observed_out_at: datetime | None = None
    estimated_out_of_range_seconds: float = 0.0
    unknown_duration_seconds: float = 0.0
    sample_count: int = 0
    observed_min_c: float | None = None
    observed_max_c: float | None = None
    censored_start: bool = False
    censored_end: bool = False
    coverage_status: CoverageStatus = CoverageStatus.full
    evidence_ids: list[str] = Field(default_factory=list)


# ── Report ──────────────────────────────────────────────────────────────────


class Hypothesis(StrictBase):
    hypothesis: HypothesisType
    assessment: Assessment
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    conflicting_evidence_ids: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    explanation: str = ""


class NextCheck(StrictBase):
    action_code: NextCheckCode
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


class Verification(StrictBase):
    status: VerificationStatus
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Report(StrictBase):
    report_id: str
    run_id: str
    snapshot_id: str
    snapshot_sha256: str
    schema_version: Literal["1.0"] = "1.0"
    detector_version: str
    prompt_version: str | None = None
    model_id: str | None = None
    created_at: datetime
    cutoff_at: datetime
    measurements: list[SensorMeasurement]
    outcome: Outcome
    primary_hypothesis: HypothesisType | None = None
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    next_checks: list[NextCheck] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    verification: Verification
    generation_mode: GenerationMode
    review_required: bool


# ── Run and infrastructure ──────────────────────────────────────────────────


class ArtifactRef(StrictBase):
    key: str
    sha256: str


class StageEvent(StrictBase):
    event_id: str
    stage: PublicStage
    tool_name: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    status: StageEventStatus
    evidence_ids: list[str] = Field(default_factory=list)


class Review(StrictBase):
    run_id: str
    actor_sub: str
    report_id: str
    decision: ReviewDecision
    note: str = ""
    created_at: datetime

    @field_validator("note")
    @classmethod
    def note_length(cls, v: str) -> str:
        if len(v) > 1000:
            raise ValueError("Note maximum 1000 characters")
        return v


class Run(StrictBase):
    run_id: str
    owner_sub: str
    status: RunStatus
    stage: PublicStage
    stage_events: list[StageEvent] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    snapshot_ref: ArtifactRef | None = None
    report_ref: ArtifactRef | None = None
    scenario_id: str | None = None
    error: str | None = None
    review: Review | None = None
    generation_mode: GenerationMode | None = None
    is_public_demo: bool = False


class RunSummary(StrictBase):
    run_id: str
    status: RunStatus
    stage: PublicStage
    scenario_id: str | None = None
    created_at: datetime
    updated_at: datetime
    outcome: Outcome | None = None
    is_public_demo: bool = False


class ClaimResult(StrictBase):
    success: bool
    reason: str = ""


# ── Queue message ───────────────────────────────────────────────────────────


class QueueMessage(StrictBase):
    run_id: str
    snapshot_id: str
    schema_version: Literal["1.0"] = "1.0"


# ── API models ──────────────────────────────────────────────────────────────


class CreateRunRequest(StrictBase):
    scenario_id: str
    seed: int | None = None


class RunResponse(StrictBase):
    run_id: str
    status: RunStatus
    poll_url: str


class ErrorResponse(StrictBase):
    error: ErrorDetail


class ErrorDetail(StrictBase):
    code: str
    message: str
    request_id: str
    retryable: bool = False


class HealthResponse(StrictBase):
    status: Literal["ok"] = "ok"
    schema_version: Literal["1.0"] = "1.0"
    build_sha: str = ""


class ReviewRequest(StrictBase):
    decision: ReviewDecision
    note: str = ""
    report_id: str

    @field_validator("note")
    @classmethod
    def note_length(cls, v: str) -> str:
        if len(v) > 1000:
            raise ValueError("Note maximum 1000 characters")
        return v
