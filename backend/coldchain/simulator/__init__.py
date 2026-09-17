"""Deterministic synthetic telemetry generation."""

from .generator import generate_snapshot
from .scenarios import SCENARIO_IDS, ScenarioId
from .validation import SnapshotValidationError, normalize_snapshot

__all__ = [
    "SCENARIO_IDS",
    "ScenarioId",
    "SnapshotValidationError",
    "generate_snapshot",
    "normalize_snapshot",
]
