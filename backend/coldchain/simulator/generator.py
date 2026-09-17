"""Pure, deterministic synthetic snapshot generator."""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any
from uuid import UUID, uuid5

from .scenarios import ScenarioSettings, settings_for
from .validation import normalize_snapshot

_ID_NAMESPACE = UUID("c5476bdf-dd49-4aad-8c67-abda694b32b5")
_OBSERVATION_MINUTES = 45
_REFERENCE_PLACEMENT = "front_air"
_COMPARISON_PLACEMENT = "rear_air"


def _parse_base_timestamp(value: datetime | str) -> datetime:
    if isinstance(value, str):
        if not value.endswith("Z"):
            raise ValueError("base_timestamp must be UTC and end in Z")
        try:
            value = datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError as exc:
            raise ValueError("base_timestamp is not a valid ISO-8601 timestamp") from exc
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("base_timestamp must be timezone-aware UTC")
    return value.astimezone(UTC).replace(microsecond=0)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _identifier_factory(
    settings: ScenarioSettings, seed: int, base: datetime
) -> Callable[[str], str]:
    prefix = f"{settings.scenario_id}|{seed}|{_timestamp(base)}"
    return lambda purpose: str(uuid5(_ID_NAMESPACE, f"{prefix}|{purpose}"))


def _noise(rng: random.Random, amplitude: float) -> float:
    return rng.uniform(-amplitude, amplitude)


def _linear(start: float, end: float, fraction: float) -> float:
    bounded = min(1.0, max(0.0, fraction))
    return start + ((end - start) * bounded)


def _normal_temperature(settings: ScenarioSettings, sensor_index: int, minute: int) -> float:
    del minute
    return settings.reference_baseline_c if sensor_index == 0 else settings.comparison_baseline_c


def _door_temperature(
    settings: ScenarioSettings,
    sensor_index: int,
    minute: int,
    open_minute: int,
    close_minute: int,
) -> float:
    baseline = (
        settings.reference_baseline_c if sensor_index == 0 else settings.comparison_baseline_c
    )
    lag = 2 + sensor_index
    rise_start = open_minute + lag
    peak = 10.6 - (sensor_index * 0.35)
    if minute <= rise_start:
        return baseline
    if minute <= rise_start + 6:
        return _linear(baseline, peak, (minute - rise_start) / 6)
    if minute <= close_minute:
        return peak
    return _linear(peak, baseline + 0.15, (minute - close_minute) / 8)


def _refrigeration_temperature(
    settings: ScenarioSettings,
    sensor_index: int,
    minute: int,
    failure_minute: int,
) -> float:
    baseline = (
        settings.reference_baseline_c if sensor_index == 0 else settings.comparison_baseline_c
    )
    rise_start = failure_minute + 2 + sensor_index
    peak = 11.2 - (sensor_index * 0.25)
    if minute <= rise_start:
        return baseline
    return _linear(baseline, peak, (minute - rise_start) / 10)


def _disagreement_temperature(
    settings: ScenarioSettings,
    sensor_index: int,
    minute: int,
    spike_start: int,
) -> float:
    baseline = (
        settings.reference_baseline_c if sensor_index == 0 else settings.comparison_baseline_c
    )
    if sensor_index == 0 or minute < spike_start:
        return baseline
    peak_minute = spike_start + 6
    end_minute = spike_start + 13
    if minute <= peak_minute:
        return _linear(baseline, 11.0, (minute - spike_start) / 6)
    if minute <= end_minute:
        return _linear(11.0, baseline, (minute - peak_minute) / 7)
    return baseline


def _ambiguous_temperature(
    settings: ScenarioSettings,
    sensor_index: int,
    minute: int,
    rise_start: int,
) -> float:
    baseline = (
        settings.reference_baseline_c if sensor_index == 0 else settings.comparison_baseline_c
    )
    peak = 9.8 - (sensor_index * 0.2)
    if minute <= rise_start + sensor_index:
        return baseline
    if minute <= rise_start + 8:
        return _linear(baseline, peak, (minute - rise_start) / 8)
    if minute <= rise_start + 18:
        return peak
    return _linear(peak, baseline + 0.1, (minute - (rise_start + 18)) / 8)


def _event(
    make_id: Callable[[str], str],
    base: datetime,
    minute: int,
    event_type: str,
    value: str,
    sequence: int,
    source: str = "simulated_controller",
) -> dict[str, Any]:
    return {
        "event_id": make_id(f"event-{sequence}"),
        "observed_at": _timestamp(base + timedelta(minutes=minute)),
        "event_type": event_type,
        "value": value,
        "source": source,
    }


def generate_snapshot(
    scenario_id: str,
    seed: int,
    base_timestamp: datetime | str,
    snapshot_id: str | None = None,
    shipment_id: str | None = None,
) -> dict[str, Any]:
    """Generate one validated 45-minute synthetic snapshot.

    Readings include both endpoints (minute 0 through minute 45), producing 46
    samples per sensor. A local ``random.Random`` instance ensures that global
    random state and the wall clock cannot affect generated telemetry.
    """

    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError("seed must be an integer")
    settings = settings_for(scenario_id)
    base = _parse_base_timestamp(base_timestamp)
    rng = random.Random(seed)
    make_id = _identifier_factory(settings, seed, base)

    sensor_ids = [make_id("sensor-reference"), make_id("sensor-comparison")]
    open_minute = 10 + rng.randrange(0, 6)
    close_minute = open_minute + 13 + rng.randrange(0, 4)
    failure_minute = 11 + rng.randrange(0, 5)
    spike_start = 15 + rng.randrange(0, 5)
    ambiguous_rise = 12 + rng.randrange(0, 6)

    events: list[dict[str, Any]] = []
    if scenario_id == "normal_control":
        events = [
            _event(make_id, base, 0, "door_state", "closed", 0),
            _event(make_id, base, 0, "refrigeration_state", "running", 1),
            _event(make_id, base, 0, "vehicle_state", "moving", 2, "simulated_telematics"),
        ]
        temperature = partial(_normal_temperature, settings)
    elif scenario_id == "door_exposure":
        events = [
            _event(make_id, base, 0, "door_state", "closed", 0),
            _event(make_id, base, 0, "refrigeration_state", "running", 1),
            _event(make_id, base, open_minute, "door_state", "open", 2),
            _event(make_id, base, close_minute, "door_state", "closed", 3),
        ]
        temperature = partial(
            _door_temperature,
            settings,
            open_minute=open_minute,
            close_minute=close_minute,
        )
    elif scenario_id == "refrigeration_problem":
        equipment_value = "fault" if rng.randrange(0, 2) else "stopped"
        events = [
            _event(make_id, base, 0, "door_state", "closed", 0),
            _event(make_id, base, 0, "refrigeration_state", "running", 1),
            _event(
                make_id,
                base,
                failure_minute,
                "refrigeration_state",
                equipment_value,
                2,
            ),
        ]
        temperature = partial(
            _refrigeration_temperature,
            settings,
            failure_minute=failure_minute,
        )
    elif scenario_id == "sensor_disagreement":
        events = [
            _event(make_id, base, 0, "door_state", "closed", 0),
            _event(make_id, base, 0, "refrigeration_state", "running", 1),
        ]
        temperature = partial(
            _disagreement_temperature,
            settings,
            spike_start=spike_start,
        )
    else:
        # Decisive door and refrigeration observations are intentionally absent.
        events = [
            _event(
                make_id,
                base,
                0,
                "vehicle_state",
                "moving",
                0,
                "simulated_telematics",
            )
        ]
        temperature = partial(
            _ambiguous_temperature,
            settings,
            rise_start=ambiguous_rise,
        )

    readings = []
    for minute in range(_OBSERVATION_MINUTES + 1):
        observed_at = _timestamp(base + timedelta(minutes=minute))
        for sensor_index, sensor_id in enumerate(sensor_ids):
            raw_temperature = temperature(sensor_index, minute)
            measured = round(raw_temperature + _noise(rng, settings.noise_amplitude_c), 2)
            readings.append(
                {
                    "event_id": make_id(f"reading-{minute}-{sensor_index}"),
                    "sensor_id": sensor_id,
                    "observed_at": observed_at,
                    "temperature_c": measured,
                }
            )

    snapshot = {
        "snapshot_id": snapshot_id or make_id("snapshot"),
        "shipment_id": shipment_id or make_id("shipment"),
        "schema_version": "1.0",
        "source": "simulated",
        "cutoff_at": _timestamp(base + timedelta(minutes=_OBSERVATION_MINUTES)),
        "policy": {
            "policy_id": "illustrative-refrigerated-1",
            "policy_version": "1.0",
            "min_c": 2.0,
            "max_c": 8.0,
            "expected_interval_seconds": 60,
            "max_gap_seconds": 120,
        },
        "sensors": [
            {
                "sensor_id": sensor_ids[0],
                "placement": _REFERENCE_PLACEMENT,
                "role": "reference",
            },
            {
                "sensor_id": sensor_ids[1],
                "placement": _COMPARISON_PLACEMENT,
                "role": "comparison",
            },
        ],
        "readings": readings,
        "events": events,
    }
    return normalize_snapshot(snapshot)
