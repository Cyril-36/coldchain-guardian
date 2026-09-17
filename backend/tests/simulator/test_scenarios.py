from datetime import UTC, datetime

import pytest

from coldchain.simulator import generate_snapshot

BASE_TIMESTAMP = datetime(2026, 9, 17, 8, 0, tzinfo=UTC)
SEEDS = range(20)


def _by_sensor(snapshot: dict) -> dict[str, list[dict]]:
    result = {sensor["sensor_id"]: [] for sensor in snapshot["sensors"]}
    for reading in snapshot["readings"]:
        result[reading["sensor_id"]].append(reading)
    return result


def _event(snapshot: dict, event_type: str, value: str) -> dict:
    return next(
        event
        for event in snapshot["events"]
        if event["event_type"] == event_type and event["value"] == value
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_normal_control_stays_inside_policy(seed: int) -> None:
    snapshot = generate_snapshot("normal_control", seed, BASE_TIMESTAMP)
    policy = snapshot["policy"]
    assert len(snapshot["sensors"]) == 2
    assert len(snapshot["readings"]) == 92
    assert all(
        policy["min_c"] <= reading["temperature_c"] <= policy["max_c"]
        for reading in snapshot["readings"]
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_door_observations_show_lag_excursion_and_recovery(seed: int) -> None:
    snapshot = generate_snapshot("door_exposure", seed, BASE_TIMESTAMP)
    opened = _event(snapshot, "door_state", "open")
    closed = [
        event
        for event in snapshot["events"]
        if event["event_type"] == "door_state" and event["value"] == "closed"
    ][-1]
    assert opened["observed_at"] < closed["observed_at"]

    for readings in _by_sensor(snapshot).values():
        before_or_at_open = [
            item["temperature_c"]
            for item in readings
            if item["observed_at"] <= opened["observed_at"]
        ]
        after_open = [
            item["temperature_c"]
            for item in readings
            if item["observed_at"] > opened["observed_at"]
        ]
        assert max(before_or_at_open) < 8.0
        assert max(after_open) > 8.0
        assert readings[-1]["temperature_c"] < 6.0


@pytest.mark.parametrize("seed", SEEDS)
def test_refrigeration_problem_has_equipment_evidence_and_two_sensor_excursion(seed: int) -> None:
    snapshot = generate_snapshot("refrigeration_problem", seed, BASE_TIMESTAMP)
    equipment_events = [
        event
        for event in snapshot["events"]
        if event["event_type"] == "refrigeration_state" and event["value"] in {"stopped", "fault"}
    ]
    assert equipment_events
    assert not any(
        event["event_type"] == "door_state" and event["value"] == "open"
        for event in snapshot["events"]
    )
    assert all(
        max(item["temperature_c"] for item in readings) > 8.0
        for readings in _by_sensor(snapshot).values()
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_sensor_disagreement_only_comparison_sensor_is_anomalous(seed: int) -> None:
    snapshot = generate_snapshot("sensor_disagreement", seed, BASE_TIMESTAMP)
    readings = _by_sensor(snapshot)
    reference_id = next(
        sensor["sensor_id"] for sensor in snapshot["sensors"] if sensor["role"] == "reference"
    )
    comparison_id = next(
        sensor["sensor_id"] for sensor in snapshot["sensors"] if sensor["role"] == "comparison"
    )
    assert max(item["temperature_c"] for item in readings[reference_id]) <= 8.0
    assert max(item["temperature_c"] for item in readings[comparison_id]) > 8.0
    assert not any(
        event["event_type"] == "refrigeration_state" and event["value"] in {"fault", "stopped"}
        for event in snapshot["events"]
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_ambiguous_incident_preserves_missing_causal_observations(seed: int) -> None:
    snapshot = generate_snapshot("ambiguous_incident", seed, BASE_TIMESTAMP)
    assert any(reading["temperature_c"] > 8.0 for reading in snapshot["readings"])
    assert not any(
        event["event_type"] in {"door_state", "refrigeration_state"} for event in snapshot["events"]
    )
