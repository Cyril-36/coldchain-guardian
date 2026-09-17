from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from coldchain.simulator import SCENARIO_IDS, generate_snapshot

BASE_TIMESTAMP = datetime(2026, 9, 17, 8, 0, tzinfo=UTC)
FORBIDDEN_MARKERS = {
    "scenario_id",
    "expected_cause",
    "expected_hypothesis",
    "ground_truth",
    "truth_label",
    "answer",
    "diagnosis",
    "caused_by_door",
    "door_failure",
}


def _strings(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)
    elif isinstance(value, str):
        yield value


def test_worker_snapshot_contains_no_hidden_truth_markers() -> None:
    for scenario_id in SCENARIO_IDS:
        snapshot = generate_snapshot(scenario_id, 42, BASE_TIMESTAMP)
        exported_strings = [value.lower() for value in _strings(snapshot)]
        for marker in FORBIDDEN_MARKERS:
            assert all(marker not in value for value in exported_strings)
        assert all(scenario_id not in value for value in exported_strings)


def test_identifiers_and_event_sources_are_opaque() -> None:
    for scenario_id in SCENARIO_IDS:
        snapshot = generate_snapshot(scenario_id, 22, BASE_TIMESTAMP)
        identifiers = [snapshot["snapshot_id"], snapshot["shipment_id"]]
        identifiers.extend(sensor["sensor_id"] for sensor in snapshot["sensors"])
        identifiers.extend(reading["event_id"] for reading in snapshot["readings"])
        identifiers.extend(event["event_id"] for event in snapshot["events"])
        assert all(scenario_id not in identifier for identifier in identifiers)
        assert all(scenario_id not in event["source"] for event in snapshot["events"])
