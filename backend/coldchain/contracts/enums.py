"""Shared enumerations for ColdChain Guardian contracts."""

from enum import Enum


# ── Snapshot enums ──────────────────────────────────────────────────────────


class EventType(str, Enum):
    door_state = "door_state"
    refrigeration_state = "refrigeration_state"
    vehicle_state = "vehicle_state"


class DoorState(str, Enum):
    open = "open"
    closed = "closed"
    unknown = "unknown"


class RefrigerationState(str, Enum):
    running = "running"
    stopped = "stopped"
    fault = "fault"
    unknown = "unknown"


class VehicleState(str, Enum):
    moving = "moving"
    stopped = "stopped"
    unknown = "unknown"


class SensorRole(str, Enum):
    reference = "reference"
    comparison = "comparison"


# ── Run / report enums ─────────────────────────────────────────────────────


class RunStatus(str, Enum):
    pending_enqueue = "pending_enqueue"
    queued = "queued"
    running = "running"
    completed = "completed"
    needs_review = "needs_review"
    failed = "failed"


class PublicStage(str, Enum):
    preparing = "preparing"
    detecting = "detecting"
    collecting_evidence = "collecting_evidence"
    comparing_hypotheses = "comparing_hypotheses"
    verifying = "verifying"
    ready = "ready"
    failed = "failed"


class StageEventStatus(str, Enum):
    started = "started"
    completed = "completed"
    failed = "failed"


class Outcome(str, Enum):
    hypothesis_supported = "hypothesis_supported"
    unresolved = "unresolved"
    no_excursion = "no_excursion"


class HypothesisType(str, Enum):
    door_exposure = "door_exposure"
    refrigeration_problem = "refrigeration_problem"
    sensor_disagreement = "sensor_disagreement"


class Assessment(str, Enum):
    supported = "supported"
    contradicted = "contradicted"
    insufficient = "insufficient"


class EvidenceKind(str, Enum):
    reading = "reading"
    event = "event"
    derived_metric = "derived_metric"
    policy = "policy"


class NextCheckCode(str, Enum):
    inspect_door = "inspect_door"
    check_refrigeration = "check_refrigeration"
    verify_sensor = "verify_sensor"
    request_missing_logs = "request_missing_logs"
    quality_review = "quality_review"


class ReviewDecision(str, Enum):
    acknowledged = "acknowledged"
    request_more_evidence = "request_more_evidence"


class GenerationMode(str, Enum):
    bedrock = "bedrock"
    deterministic_only = "deterministic_only"


class VerificationStatus(str, Enum):
    passed = "passed"
    blocked = "blocked"


class CoverageStatus(str, Enum):
    full = "full"
    partial = "partial"
    unknown = "unknown"
