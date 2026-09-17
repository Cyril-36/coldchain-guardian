from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from coldchain.simulator import SnapshotValidationError, normalize_snapshot


def test_normalization_sorts_and_deduplicates_identical_records(
    mutable_normal_snapshot: dict,
) -> None:
    duplicate = deepcopy(mutable_normal_snapshot["readings"][0])
    mutable_normal_snapshot["readings"] = list(reversed(mutable_normal_snapshot["readings"]))
    mutable_normal_snapshot["readings"].append(duplicate)
    normalized = normalize_snapshot(mutable_normal_snapshot)
    assert len(normalized["readings"]) == 92
    order = [(item["observed_at"], item["event_id"]) for item in normalized["readings"]]
    assert order == sorted(order)


def test_normalization_sorts_by_instant_before_event_id(mutable_normal_snapshot: dict) -> None:
    later = mutable_normal_snapshot["readings"][0]
    earlier = mutable_normal_snapshot["readings"][1]
    later["observed_at"] = "2026-09-17T08:00:00.500000Z"
    earlier["observed_at"] = "2026-09-17T08:00:00Z"
    normalized = normalize_snapshot(mutable_normal_snapshot)
    assert normalized["readings"].index(earlier) < normalized["readings"].index(later)


@pytest.mark.parametrize("temperature", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_temperature_is_rejected(
    mutable_normal_snapshot: dict, temperature: float
) -> None:
    mutable_normal_snapshot["readings"][0]["temperature_c"] = temperature
    with pytest.raises(SnapshotValidationError, match="finite"):
        normalize_snapshot(mutable_normal_snapshot)


def test_invalid_timestamp_is_rejected(mutable_normal_snapshot: dict) -> None:
    mutable_normal_snapshot["readings"][0]["observed_at"] = "not-a-timestamp"
    with pytest.raises(SnapshotValidationError, match="timestamp"):
        normalize_snapshot(mutable_normal_snapshot)


@pytest.mark.parametrize("collection", ["readings", "events"])
def test_record_after_cutoff_is_rejected(mutable_normal_snapshot: dict, collection: str) -> None:
    cutoff = datetime.fromisoformat(mutable_normal_snapshot["cutoff_at"].replace("Z", "+00:00"))
    mutable_normal_snapshot[collection][0]["observed_at"] = (
        (cutoff + timedelta(seconds=1)).astimezone(UTC).isoformat().replace("+00:00", "Z")
    )
    with pytest.raises(SnapshotValidationError, match="after cutoff"):
        normalize_snapshot(mutable_normal_snapshot)


def test_unknown_sensor_is_rejected(mutable_normal_snapshot: dict) -> None:
    mutable_normal_snapshot["readings"][0]["sensor_id"] = str(uuid4())
    with pytest.raises(SnapshotValidationError, match="unknown sensor"):
        normalize_snapshot(mutable_normal_snapshot)


def test_conflicting_duplicate_id_is_rejected(mutable_normal_snapshot: dict) -> None:
    conflict = deepcopy(mutable_normal_snapshot["readings"][0])
    conflict["temperature_c"] += 1.0
    mutable_normal_snapshot["readings"].append(conflict)
    with pytest.raises(SnapshotValidationError, match="conflicting duplicate"):
        normalize_snapshot(mutable_normal_snapshot)


def test_duplicate_id_across_record_kinds_is_rejected(mutable_normal_snapshot: dict) -> None:
    mutable_normal_snapshot["events"][0]["event_id"] = mutable_normal_snapshot["readings"][0][
        "event_id"
    ]
    with pytest.raises(SnapshotValidationError, match="conflicting duplicate"):
        normalize_snapshot(mutable_normal_snapshot)


def test_too_many_readings_are_rejected(mutable_normal_snapshot: dict) -> None:
    template = mutable_normal_snapshot["readings"][0]
    mutable_normal_snapshot["readings"] = [
        {**template, "event_id": str(uuid4())} for _ in range(2_001)
    ]
    with pytest.raises(SnapshotValidationError, match="2000 readings"):
        normalize_snapshot(mutable_normal_snapshot)


def test_too_many_events_are_rejected(mutable_normal_snapshot: dict) -> None:
    template = mutable_normal_snapshot["events"][0]
    mutable_normal_snapshot["events"] = [{**template, "event_id": str(uuid4())} for _ in range(201)]
    with pytest.raises(SnapshotValidationError, match="200 events"):
        normalize_snapshot(mutable_normal_snapshot)


def test_payload_over_one_mib_is_rejected(mutable_normal_snapshot: dict) -> None:
    mutable_normal_snapshot["sensors"][0]["placement"] = "x" * 1_048_576
    with pytest.raises(SnapshotValidationError, match="1 MiB"):
        normalize_snapshot(mutable_normal_snapshot)


def test_exactly_one_reference_sensor_is_required(mutable_normal_snapshot: dict) -> None:
    mutable_normal_snapshot["sensors"][1]["role"] = "reference"
    with pytest.raises(SnapshotValidationError, match="exactly one reference"):
        normalize_snapshot(mutable_normal_snapshot)


def test_wrong_schema_version_is_rejected(mutable_normal_snapshot: dict) -> None:
    mutable_normal_snapshot["schema_version"] = "2.0"
    with pytest.raises(SnapshotValidationError, match="schema_version"):
        normalize_snapshot(mutable_normal_snapshot)


def test_unknown_fields_are_rejected(mutable_normal_snapshot: dict) -> None:
    mutable_normal_snapshot["expected_cause"] = "anything"
    with pytest.raises(SnapshotValidationError, match="unknown"):
        normalize_snapshot(mutable_normal_snapshot)
