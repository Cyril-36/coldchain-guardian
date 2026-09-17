"""Deterministic detection core — no AWS or model imports."""

from coldchain.core.detector import (
    DETECTOR_VERSION,
    DetectionResult,
    build_detection_result,
    build_measurement_evidence,
    check_multiple_windows,
    detect_excursions,
    detect_excursions_with_evidence,
    is_excursion_detected,
    validate_snapshot,
)

__all__ = [
    "DETECTOR_VERSION",
    "DetectionResult",
    "build_detection_result",
    "build_measurement_evidence",
    "check_multiple_windows",
    "detect_excursions",
    "detect_excursions_with_evidence",
    "is_excursion_detected",
    "validate_snapshot",
]
