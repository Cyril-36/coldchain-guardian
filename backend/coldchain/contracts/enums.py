"""Shared enumerations for ColdChain Guardian contracts.

Source of truth: docs/CONTRACTS.md
All string values use snake_case matching the canonical JSON contracts.
"""

from enum import StrEnum

# ── Snapshot Enums ──────────────────────────────────────────────────────────


class EventType(StrEnum):
    door_state = "door_state"
    refrigeration_state = "refrigeration_state"
    vehicle_state = "vehicle_state"


class DoorState(StrEnum):
    open = "open"
    closed = "closed"
    unknown = "unknown"


class RefrigerationState(StrEnum):
    running = "running"
    stopped = "stopped"
    fault = "fault"
    unknown = "unknown"


class VehicleState(StrEnum):
    moving = "moving"
    stopped = "stopped"
    unknown = "unknown"


class SensorRole(StrEnum):
    reference = "reference"
    comparison = "comparison"


# ── State Machine & Run Enums ───────────────────────────────────────────────


class RunStatus(StrEnum):
    pending_enqueue = "pending_enqueue"
    queued = "queued"
    running = "running"
    completed = "completed"
    needs_review = "needs_review"
    failed = "failed"


class PublicStage(StrEnum):
    preparing = "preparing"
    detecting = "detecting"
    collecting_evidence = "collecting_evidence"
    comparing_hypotheses = "comparing_hypotheses"
    verifying = "verifying"
    ready = "ready"
    failed = "failed"


class StageEventStatus(StrEnum):
    started = "started"
    completed = "completed"
    failed = "failed"


# ── Report & Evidence Enums ─────────────────────────────────────────────────


class Outcome(StrEnum):
    hypothesis_supported = "hypothesis_supported"
    unresolved = "unresolved"
    no_excursion = "no_excursion"


class HypothesisType(StrEnum):
    door_exposure = "door_exposure"
    refrigeration_problem = "refrigeration_problem"
    sensor_disagreement = "sensor_disagreement"


class Assessment(StrEnum):
    supported = "supported"
    contradicted = "contradicted"
    insufficient = "insufficient"


class EvidenceKind(StrEnum):
    reading = "reading"
    event = "event"
    derived_metric = "derived_metric"
    policy = "policy"


class NextCheckCode(StrEnum):
    inspect_door = "inspect_door"
    check_refrigeration = "check_refrigeration"
    verify_sensor = "verify_sensor"
    request_missing_logs = "request_missing_logs"
    quality_review = "quality_review"


class ReviewDecision(StrEnum):
    acknowledged = "acknowledged"
    request_more_evidence = "request_more_evidence"


class GenerationMode(StrEnum):
    bedrock = "bedrock"
    deterministic_only = "deterministic_only"


class VerificationStatus(StrEnum):
    passed = "passed"
    blocked = "blocked"


class CoverageStatus(StrEnum):
    complete = "complete"
    partial = "partial"
    insufficient = "insufficient"
