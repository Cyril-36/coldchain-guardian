import random
from datetime import UTC, datetime

from coldchain.simulator import SCENARIO_IDS, generate_snapshot

BASE_TIMESTAMP = datetime(2026, 9, 17, 8, 0, tzinfo=UTC)


def _observations(snapshot: dict) -> dict:
    return {
        "cutoff_at": snapshot["cutoff_at"],
        "policy": snapshot["policy"],
        "sensors": snapshot["sensors"],
        "readings": snapshot["readings"],
        "events": snapshot["events"],
    }


def test_same_inputs_reproduce_every_scenario() -> None:
    for scenario_id in SCENARIO_IDS:
        first = generate_snapshot(scenario_id, 1234, BASE_TIMESTAMP)
        second = generate_snapshot(scenario_id, 1234, BASE_TIMESTAMP)
        assert first == second


def test_supplied_opaque_ids_do_not_change_observations() -> None:
    first = generate_snapshot(
        "door_exposure",
        77,
        BASE_TIMESTAMP,
        snapshot_id="66a5eda5-310c-41fe-9f96-dad00e511405",
        shipment_id="43d93337-177f-4be8-ae27-80947ecdc087",
    )
    second = generate_snapshot(
        "door_exposure",
        77,
        BASE_TIMESTAMP,
        snapshot_id="9c8fa033-0b0f-4677-a771-a049f25dfa7a",
        shipment_id="61ba9da0-9f79-424c-963f-a87e1d3db48f",
    )
    assert _observations(first) == _observations(second)


def test_different_seeds_produce_controlled_variation() -> None:
    first = generate_snapshot("door_exposure", 1, BASE_TIMESTAMP)
    second = generate_snapshot("door_exposure", 2, BASE_TIMESTAMP)
    first_temperatures = [reading["temperature_c"] for reading in first["readings"]]
    second_temperatures = [reading["temperature_c"] for reading in second["readings"]]
    assert first_temperatures != second_temperatures
    assert all(2.0 <= value <= 11.0 for value in first_temperatures + second_temperatures)


def test_generation_does_not_mutate_global_random_state() -> None:
    before = random.getstate()
    generate_snapshot("door_exposure", 123, BASE_TIMESTAMP)
    assert random.getstate() == before
