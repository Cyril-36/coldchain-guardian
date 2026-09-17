"""Internal scenario configuration.

Scenario identifiers belong to the control layer. They must never be copied into
the generated snapshot returned to a worker or investigator.
"""

from dataclasses import dataclass
from typing import Literal, cast

ScenarioId = Literal[
    "normal_control",
    "door_exposure",
    "refrigeration_problem",
    "sensor_disagreement",
    "ambiguous_incident",
]

SCENARIO_IDS: tuple[ScenarioId, ...] = (
    "normal_control",
    "door_exposure",
    "refrigeration_problem",
    "sensor_disagreement",
    "ambiguous_incident",
)


@dataclass(frozen=True, slots=True)
class ScenarioSettings:
    """Generator-only settings that are not part of exported telemetry."""

    scenario_id: ScenarioId
    reference_baseline_c: float = 5.0
    comparison_baseline_c: float = 5.2
    noise_amplitude_c: float = 0.16


def settings_for(scenario_id: str) -> ScenarioSettings:
    """Return internal settings for a supported public scenario identifier."""

    if scenario_id not in SCENARIO_IDS:
        supported = ", ".join(SCENARIO_IDS)
        raise ValueError(f"unsupported scenario_id; expected one of: {supported}")
    return ScenarioSettings(scenario_id=cast(ScenarioId, scenario_id))
