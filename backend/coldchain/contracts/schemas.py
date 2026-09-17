"""Pydantic v2 schemas for ColdChain Guardian contracts.

Source of truth: docs/CONTRACTS.md
Rules:
- JSON field names use snake_case.
- Timestamps are ISO 8601 UTC ending in Z.
- IDs are opaque, normalized lowercase UUID strings.
- Temperatures use Celsius, durations use seconds, all numbers must be finite.
- Unknown fields are strictly forbidden (extra="forbid").
- Snapshots are limited to 2,000 readings, 200 events, and 1 MiB serialized JSON.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)
from pydantic.functional_validators import AfterValidator, BeforeValidator

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

# ── Validation Helpers & Annotated Types ────────────────────────────────────


def _validate_uuid_str(v: Any) -> str:
    if not isinstance(v, str):
        raise ValueError("Identifier must be a string UUID")
    try:
        parsed = UUID(v)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid UUID: {v}") from exc
    if str(parsed) != v:
        raise ValueError(f"UUID must be normalized lowercase format: {v}")
    return v


def _validate_utc_z_datetime(v: Any) -> datetime:
    if isinstance(v, str):
        if not v.endswith("Z"):
            raise ValueError(f"Timestamp must be an ISO 8601 UTC string ending in Z: {v}")
        try:
            dt = datetime.fromisoformat(v[:-1] + "+00:00")
        except ValueError as exc:
            raise ValueError(f"Invalid ISO 8601 timestamp: {v}") from exc
    elif isinstance(v, datetime):
        if v.tzinfo is None or v.utcoffset() != timedelta(0):
            raise ValueError("Timestamp must be timezone-aware UTC")
        dt = v
    else:
        raise ValueError(
            f"Timestamp must be an ISO 8601 UTC string ending in Z, got {type(v).__name__}"
        )
    return dt


def _serialize_utc_z_datetime(dt: datetime) -> str:
    s = dt.astimezone(UTC).isoformat()
    return s.replace("+00:00", "Z")


def _validate_finite_float(v: Any) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise ValueError("Value must be numeric")
    f = float(v)
    if not math.isfinite(f):
        raise ValueError("Numeric value must be finite (NaN and infinity are rejected)")
    return f


def _validate_sha256_digest(v: str) -> str:
    if not isinstance(v, str) or len(v) != 64 or not all(c in "0123456789abcdef" for c in v):
        raise ValueError("Must be a 64-character lowercase hex SHA-256 digest")
    return v


UuidStr = Annotated[str, BeforeValidator(_validate_uuid_str)]
UtcDatetime = Annotated[
    datetime,
    BeforeValidator(_validate_utc_z_datetime),
    PlainSerializer(_serialize_utc_z_datetime, return_type=str, when_used="json-unless-none"),
]
FiniteFloat = Annotated[float, BeforeValidator(_validate_finite_float)]
Sha256Str = Annotated[str, AfterValidator(_validate_sha256_digest)]


# ── Base Model ──────────────────────────────────────────────────────────────


class StrictBase(BaseModel):
    """Base model that strictly rejects extra/unknown fields."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# ── Snapshot Models ─────────────────────────────────────────────────────────


class Policy(StrictBase):
    policy_id: str
    policy_version: str
    min_c: FiniteFloat
    max_c: FiniteFloat
    expected_interval_seconds: FiniteFloat
    max_gap_seconds: FiniteFloat

    @model_validator(mode="after")
    def validate_policy_rules(self) -> Policy:
        if self.min_c >= self.max_c:
            raise ValueError(f"min_c ({self.min_c}) must be strictly below max_c ({self.max_c})")
        if self.expected_interval_seconds <= 0:
            raise ValueError("expected_interval_seconds must be positive")
        if self.max_gap_seconds <= 0:
            raise ValueError("max_gap_seconds must be positive")
        return self


class Sensor(StrictBase):
    sensor_id: UuidStr
    placement: str
    role: SensorRole

    @field_validator("placement")
    @classmethod
    def validate_placement_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("placement must be a non-empty string")
        return v


class Reading(StrictBase):
    event_id: UuidStr
    sensor_id: UuidStr
    observed_at: UtcDatetime
    temperature_c: FiniteFloat


_EVENT_VALUES: dict[EventType, set[str]] = {
    EventType.door_state: {"open", "closed", "unknown"},
    EventType.refrigeration_state: {"running", "stopped", "fault", "unknown"},
    EventType.vehicle_state: {"moving", "stopped", "unknown"},
}


class Event(StrictBase):
    event_id: UuidStr
    observed_at: UtcDatetime
    event_type: EventType
    value: str
    source: str

    @field_validator("source")
    @classmethod
    def validate_source_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("source must be a non-empty string")
        return v

    @model_validator(mode="after")
    def validate_event_value(self) -> Event:
        allowed = _EVENT_VALUES.get(self.event_type, set())
        if self.value not in allowed:
            raise ValueError(
                f"Value '{self.value}' is invalid for event_type '{self.event_type.value}'; "
                f"allowed: {sorted(allowed)}"
            )
        return self


MAX_SNAPSHOT_READINGS = 2000
MAX_SNAPSHOT_EVENTS = 200
MAX_SNAPSHOT_SERIALIZED_BYTES = 1_048_576


class Snapshot(StrictBase):
    snapshot_id: UuidStr
    shipment_id: UuidStr
    schema_version: Literal["1.0"] = "1.0"
    source: Literal["simulated"] = "simulated"
    cutoff_at: UtcDatetime
    policy: Policy
    sensors: list[Sensor]
    readings: list[Reading]
    events: list[Event]

    @model_validator(mode="after")
    def validate_snapshot_rules(self) -> Snapshot:
        # Sensor constraints: exactly one reference
        ref_count = sum(1 for s in self.sensors if s.role == SensorRole.reference)
        if ref_count != 1:
            raise ValueError(f"Snapshot must have exactly one reference sensor; found {ref_count}")

        sensor_ids: set[str] = set()
        for s in self.sensors:
            if s.sensor_id in sensor_ids:
                raise ValueError(f"Duplicate sensor_id in snapshot: {s.sensor_id}")
            sensor_ids.add(s.sensor_id)

        # Count limits
        if len(self.readings) > MAX_SNAPSHOT_READINGS:
            raise ValueError(
                f"Readings count ({len(self.readings)}) exceeds max {MAX_SNAPSHOT_READINGS}"
            )
        if len(self.events) > MAX_SNAPSHOT_EVENTS:
            raise ValueError(
                f"Events count ({len(self.events)}) exceeds max {MAX_SNAPSHOT_EVENTS}"
            )

        # Readings validation: sensor existence and cutoff
        for idx, r in enumerate(self.readings):
            if r.sensor_id not in sensor_ids:
                raise ValueError(
                    f"Reading at index {idx} references unknown sensor_id: {r.sensor_id}"
                )
            if r.observed_at > self.cutoff_at:
                raise ValueError(
                    f"Reading {r.event_id} observed_at {r.observed_at} occurs after "
                    f"cutoff {self.cutoff_at}"
                )

        # Events validation: cutoff
        for e in self.events:
            if e.observed_at > self.cutoff_at:
                raise ValueError(
                    f"Event {e.event_id} observed_at {e.observed_at} occurs after "
                    f"cutoff {self.cutoff_at}"
                )

        # Duplicate ID check: identical duplicates allowed, conflicting duplicates rejected
        seen: dict[str, tuple[str, dict[str, Any]]] = {}
        for r in self.readings:
            serialized_reading = r.model_dump(mode="json")
            if r.event_id in seen:
                kind, prior = seen[r.event_id]
                if kind != "reading" or prior != serialized_reading:
                    raise ValueError(f"Conflicting duplicate event_id: {r.event_id}")
            else:
                seen[r.event_id] = ("reading", serialized_reading)

        for e in self.events:
            serialized_event = e.model_dump(mode="json")
            if e.event_id in seen:
                kind, prior = seen[e.event_id]
                if kind != "event" or prior != serialized_event:
                    raise ValueError(f"Conflicting duplicate event_id: {e.event_id}")
            else:
                seen[e.event_id] = ("event", serialized_event)

        # 1 MiB serialized JSON limit
        encoded = json.dumps(
            self.model_dump(mode="json"),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(encoded) > MAX_SNAPSHOT_SERIALIZED_BYTES:
            raise ValueError(
                f"Snapshot serialized JSON size ({len(encoded)} bytes) exceeds "
                f"1 MiB limit ({MAX_SNAPSHOT_SERIALIZED_BYTES} bytes)"
            )

        return self


def snapshot_sha256(snapshot: Snapshot) -> str:
    """Compute the canonical SHA-256 digest of a snapshot's serialized JSON bytes."""
    encoded = json.dumps(
        snapshot.model_dump(mode="json"),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# ── Evidence Models ─────────────────────────────────────────────────────────


class EvidenceRef(StrictBase):
    evidence_id: UuidStr
    snapshot_id: UuidStr
    kind: EvidenceKind
    record_ids: list[UuidStr] = Field(default_factory=list)
    observed_at: UtcDatetime | None = None
    interval_start: UtcDatetime | None = None
    interval_end: UtcDatetime | None = None
    summary: str
    method_version: str | None = None
    input_record_ids: list[UuidStr] | None = None


# ── Measurement Models ──────────────────────────────────────────────────────


class SensorMeasurement(StrictBase):
    sensor_id: UuidStr
    role: SensorRole
    excursion_detected: bool
    first_observed_out_at: UtcDatetime | None = None
    last_observed_out_at: UtcDatetime | None = None
    estimated_out_of_range_seconds: FiniteFloat = 0.0
    unknown_duration_seconds: FiniteFloat = 0.0
    sample_count: int = 0
    observed_min_c: FiniteFloat | None = None
    observed_max_c: FiniteFloat | None = None
    censored_start: bool = False
    censored_end: bool = False
    coverage_status: CoverageStatus = CoverageStatus.full
    evidence_ids: list[UuidStr] = Field(default_factory=list)


# ── Report Models ───────────────────────────────────────────────────────────


class Hypothesis(StrictBase):
    hypothesis: HypothesisType
    assessment: Assessment
    supporting_evidence_ids: list[UuidStr] = Field(default_factory=list)
    conflicting_evidence_ids: list[UuidStr] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    explanation: str = ""


class NextCheck(StrictBase):
    action_code: NextCheckCode = Field(
        validation_alias=AliasChoices("action_code", "code")
    )
    reason: str
    evidence_ids: list[UuidStr] = Field(
        default_factory=list,
        validation_alias=AliasChoices("evidence_ids", "related_evidence_ids"),
    )


class Verification(StrictBase):
    status: VerificationStatus
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class Report(StrictBase):
    report_id: UuidStr
    run_id: UuidStr
    snapshot_id: UuidStr
    snapshot_sha256: Sha256Str
    schema_version: Literal["1.0"] = "1.0"
    detector_version: str
    prompt_version: str | None = None
    model_id: str | None = None
    created_at: UtcDatetime
    cutoff_at: UtcDatetime
    measurements: list[SensorMeasurement]
    outcome: Outcome
    primary_hypothesis: HypothesisType | None = None
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    next_checks: list[NextCheck] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    verification: Verification
    generation_mode: GenerationMode
    review_required: bool


# ── Storage & Run Models ────────────────────────────────────────────────────


class ArtifactRef(StrictBase):
    key: str
    sha256: Sha256Str


class StageEvent(StrictBase):
    event_id: UuidStr
    stage: PublicStage
    tool_name: str | None = None
    started_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    status: StageEventStatus
    evidence_ids: list[UuidStr] = Field(default_factory=list)


class Review(StrictBase):
    review_id: UuidStr = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: UuidStr
    actor_sub: str
    report_id: UuidStr
    decision: ReviewDecision
    note: str = Field(default="", max_length=1000)
    created_at: UtcDatetime


class Run(StrictBase):
    run_id: UuidStr
    owner_sub: str
    status: RunStatus
    stage: PublicStage
    stage_events: list[StageEvent] = Field(default_factory=list)
    created_at: UtcDatetime
    updated_at: UtcDatetime
    snapshot_ref: ArtifactRef | None = None
    report_ref: ArtifactRef | None = None
    scenario_id: str | None = None
    error: str | None = None
    review: Review | None = None
    generation_mode: GenerationMode | None = None
    is_public_demo: bool = False
    attempt_id: str | None = None
    lease_expires_at: UtcDatetime | None = None

    @field_validator("stage_events")
    @classmethod
    def validate_stage_events_limit(cls, v: list[StageEvent]) -> list[StageEvent]:
        if len(v) > 40:
            raise ValueError(f"stage_events cannot exceed 40 items; got {len(v)}")
        return v


class RunSummary(StrictBase):
    run_id: UuidStr
    status: RunStatus
    stage: PublicStage
    scenario_id: str | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime
    outcome: Outcome | None = None
    is_public_demo: bool = False


class ClaimResult(StrictBase):
    success: bool
    reason: str = ""


class QueueMessage(StrictBase):
    run_id: UuidStr
    snapshot_id: UuidStr
    schema_version: Literal["1.0"] = "1.0"


# ── HTTP API Request & Response Models ──────────────────────────────────────


class CreateRunRequest(StrictBase):
    scenario_id: str
    seed: int | None = None


class RunResponse(StrictBase):
    run_id: UuidStr
    status: RunStatus
    poll_url: str


class ErrorDetail(StrictBase):
    code: str
    message: str
    request_id: str
    retryable: bool = False


class ErrorResponse(StrictBase):
    error: ErrorDetail


class HealthResponse(StrictBase):
    status: Literal["ok"] = "ok"
    schema_version: Literal["1.0"] = "1.0"
    build_sha: str = ""


class ReviewRequest(StrictBase):
    decision: ReviewDecision
    note: str = Field(default="", max_length=1000)
    report_id: UuidStr
