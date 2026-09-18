"""Strict normalization for simulator-produced snapshots.

This is intentionally scoped to the simulator. Cyril's canonical Pydantic
contracts have not been published yet; this module can be replaced by an import
of those models without introducing a second ``coldchain.contracts`` package.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import UUID

SCHEMA_VERSION = "1.0"
MAX_READINGS = 2_000
MAX_EVENTS = 200
MAX_SERIALIZED_BYTES = 1_048_576

_TOP_LEVEL_FIELDS = {
    "snapshot_id",
    "shipment_id",
    "schema_version",
    "source",
    "cutoff_at",
    "policy",
    "sensors",
    "readings",
    "events",
}
_POLICY_FIELDS = {
    "policy_id",
    "policy_version",
    "min_c",
    "max_c",
    "expected_interval_seconds",
    "max_gap_seconds",
}
_SENSOR_FIELDS = {"sensor_id", "placement", "role"}
_READING_FIELDS = {"event_id", "sensor_id", "observed_at", "temperature_c"}
_EVENT_FIELDS = {"event_id", "observed_at", "event_type", "value", "source"}
_EVENT_VALUES = {
    "door_state": {"open", "closed", "unknown"},
    "refrigeration_state": {"running", "stopped", "fault", "unknown"},
    "vehicle_state": {"moving", "stopped", "unknown"},
}


class SnapshotValidationError(ValueError):
    """Raised when a snapshot violates the frozen v1 document contract."""


def _require_mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SnapshotValidationError(f"{location} must be an object")
    return value


def _require_exact_fields(value: Mapping[str, Any], expected: set[str], location: str) -> None:
    fields = set(value)
    if fields == expected:
        return
    missing = sorted(expected - fields)
    unknown = sorted(fields - expected)
    details = []
    if missing:
        details.append(f"missing {missing}")
    if unknown:
        details.append(f"unknown {unknown}")
    raise SnapshotValidationError(f"{location} has invalid fields: {', '.join(details)}")


def _require_uuid(value: Any, location: str) -> str:
    if not isinstance(value, str):
        raise SnapshotValidationError(f"{location} must be a UUID string")
    try:
        UUID(value)
    except (ValueError, AttributeError) as exc:
        raise SnapshotValidationError(f"{location} must be a UUID string") from exc
    return value


def _parse_timestamp(value: Any, location: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SnapshotValidationError(f"{location} must be an ISO-8601 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise SnapshotValidationError(f"{location} is not a valid timestamp") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise SnapshotValidationError(f"{location} must be UTC")
    return parsed


def _require_finite_number(value: Any, location: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise SnapshotValidationError(f"{location} must be numeric")
    if not math.isfinite(value):
        raise SnapshotValidationError(f"{location} must be finite")
    return value


def _serialized_size(snapshot: Mapping[str, Any]) -> int:
    try:
        encoded = json.dumps(
            snapshot,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SnapshotValidationError("snapshot must contain JSON-safe finite values") from exc
    return len(encoded)


def _deduplicate_records(
    records: list[dict[str, Any]],
    seen_by_id: dict[str, tuple[str, dict[str, Any]]],
    record_kind: str,
) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    for record in records:
        event_id = record["event_id"]
        prior = seen_by_id.get(event_id)
        if prior is None:
            seen_by_id[event_id] = (record_kind, record)
            unique.append(record)
        elif prior != (record_kind, record):
            raise SnapshotValidationError(f"conflicting duplicate event_id: {event_id}")
    return unique


def normalize_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Validate, deduplicate, and deterministically sort a v1 snapshot.

    The input is not mutated. Identical records sharing an event ID are reduced
    to one record; reuse of an ID for different content is rejected.
    """

    source = _require_mapping(snapshot, "snapshot")
    if _serialized_size(source) > MAX_SERIALIZED_BYTES:
        raise SnapshotValidationError("snapshot exceeds the 1 MiB serialized JSON limit")
    _require_exact_fields(source, _TOP_LEVEL_FIELDS, "snapshot")

    normalized = deepcopy(dict(source))
    _require_uuid(normalized["snapshot_id"], "snapshot.snapshot_id")
    _require_uuid(normalized["shipment_id"], "snapshot.shipment_id")
    if normalized["schema_version"] != SCHEMA_VERSION:
        raise SnapshotValidationError(f"schema_version must be {SCHEMA_VERSION}")
    if normalized["source"] != "simulated":
        raise SnapshotValidationError("source must be simulated for the MVP")
    cutoff = _parse_timestamp(normalized["cutoff_at"], "snapshot.cutoff_at")

    policy = _require_mapping(normalized["policy"], "snapshot.policy")
    _require_exact_fields(policy, _POLICY_FIELDS, "snapshot.policy")
    if not isinstance(policy["policy_id"], str) or not policy["policy_id"]:
        raise SnapshotValidationError("snapshot.policy.policy_id must be a non-empty string")
    if not isinstance(policy["policy_version"], str) or not policy["policy_version"]:
        raise SnapshotValidationError("snapshot.policy.policy_version must be a non-empty string")
    minimum = _require_finite_number(policy["min_c"], "snapshot.policy.min_c")
    maximum = _require_finite_number(policy["max_c"], "snapshot.policy.max_c")
    if minimum >= maximum:
        raise SnapshotValidationError("snapshot.policy.min_c must be below max_c")
    for field in ("expected_interval_seconds", "max_gap_seconds"):
        interval = _require_finite_number(policy[field], f"snapshot.policy.{field}")
        if interval <= 0:
            raise SnapshotValidationError(f"snapshot.policy.{field} must be positive")

    sensors = normalized["sensors"]
    if not isinstance(sensors, list) or len(sensors) != 2:
        raise SnapshotValidationError("snapshot.sensors must contain exactly two sensors")
    sensor_ids: set[str] = set()
    reference_count = 0
    for index, raw_sensor in enumerate(sensors):
        sensor = _require_mapping(raw_sensor, f"snapshot.sensors[{index}]")
        _require_exact_fields(sensor, _SENSOR_FIELDS, f"snapshot.sensors[{index}]")
        sensor_id = _require_uuid(sensor["sensor_id"], f"snapshot.sensors[{index}].sensor_id")
        if sensor_id in sensor_ids:
            raise SnapshotValidationError(f"duplicate sensor_id: {sensor_id}")
        sensor_ids.add(sensor_id)
        if not isinstance(sensor["placement"], str) or not sensor["placement"]:
            raise SnapshotValidationError(f"snapshot.sensors[{index}].placement is required")
        if sensor["role"] not in {"reference", "comparison"}:
            raise SnapshotValidationError(
                f"snapshot.sensors[{index}].role must be reference or comparison"
            )
        reference_count += sensor["role"] == "reference"
    if reference_count != 1:
        raise SnapshotValidationError("snapshot must configure exactly one reference sensor")

    readings = normalized["readings"]
    events = normalized["events"]
    if not isinstance(readings, list):
        raise SnapshotValidationError("snapshot.readings must be a list")
    if not isinstance(events, list):
        raise SnapshotValidationError("snapshot.events must be a list")
    if len(readings) > MAX_READINGS:
        raise SnapshotValidationError(f"snapshot exceeds {MAX_READINGS} readings")
    if len(events) > MAX_EVENTS:
        raise SnapshotValidationError(f"snapshot exceeds {MAX_EVENTS} events")

    checked_readings: list[dict[str, Any]] = []
    for index, raw_reading in enumerate(readings):
        location = f"snapshot.readings[{index}]"
        reading = dict(_require_mapping(raw_reading, location))
        _require_exact_fields(reading, _READING_FIELDS, location)
        _require_uuid(reading["event_id"], f"{location}.event_id")
        if reading["sensor_id"] not in sensor_ids:
            raise SnapshotValidationError(f"{location}.sensor_id references an unknown sensor")
        observed_at = _parse_timestamp(reading["observed_at"], f"{location}.observed_at")
        if observed_at > cutoff:
            raise SnapshotValidationError(f"{location} occurs after cutoff_at")
        _require_finite_number(reading["temperature_c"], f"{location}.temperature_c")
        checked_readings.append(reading)

    checked_events: list[dict[str, Any]] = []
    for index, raw_event in enumerate(events):
        location = f"snapshot.events[{index}]"
        event = dict(_require_mapping(raw_event, location))
        _require_exact_fields(event, _EVENT_FIELDS, location)
        _require_uuid(event["event_id"], f"{location}.event_id")
        observed_at = _parse_timestamp(event["observed_at"], f"{location}.observed_at")
        if observed_at > cutoff:
            raise SnapshotValidationError(f"{location} occurs after cutoff_at")
        event_type = event["event_type"]
        if event_type not in _EVENT_VALUES:
            raise SnapshotValidationError(f"{location}.event_type is unsupported")
        if event["value"] not in _EVENT_VALUES[event_type]:
            raise SnapshotValidationError(f"{location}.value is invalid for {event_type}")
        if not isinstance(event["source"], str) or not event["source"]:
            raise SnapshotValidationError(f"{location}.source must be a non-empty string")
        checked_events.append(event)

    seen_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
    checked_readings = _deduplicate_records(checked_readings, seen_by_id, "reading")
    checked_events = _deduplicate_records(checked_events, seen_by_id, "event")
    checked_readings.sort(
        key=lambda record: (
            _parse_timestamp(record["observed_at"], "reading.observed_at"),
            record["event_id"],
        )
    )
    checked_events.sort(
        key=lambda record: (
            _parse_timestamp(record["observed_at"], "event.observed_at"),
            record["event_id"],
        )
    )
    normalized["readings"] = checked_readings
    normalized["events"] = checked_events

    if _serialized_size(normalized) > MAX_SERIALIZED_BYTES:
        raise SnapshotValidationError("snapshot exceeds the 1 MiB serialized JSON limit")
    return normalized
