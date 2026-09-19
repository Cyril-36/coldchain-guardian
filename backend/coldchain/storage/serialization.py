"""Canonical Pydantic artifact serialization and digest verification."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from pydantic import BaseModel

from coldchain.contracts.schemas import Report, Run

from .exceptions import ConditionalCheckFailedError, StateConflictError

ArtifactModel = TypeVar("ArtifactModel", bound=BaseModel)
ArtifactValidator = Callable[[Any], BaseModel]


def validate_report_binding(run: Run, report: Report) -> None:
    """Require a report to describe the run's exact frozen snapshot."""
    if run.snapshot_ref is None:
        raise ConditionalCheckFailedError(
            "run cannot complete before its snapshot reference is attached"
        )

    mismatches: list[str] = []
    if report.run_id != run.run_id:
        mismatches.append("run_id")
    if report.snapshot_id != run.snapshot_id:
        mismatches.append("snapshot_id")
    if report.snapshot_sha256 != run.snapshot_ref.sha256:
        mismatches.append("snapshot_sha256")
    if mismatches:
        raise ConditionalCheckFailedError(
            "report does not match the current run and attached snapshot: " + ", ".join(mismatches)
        )


def canonical_json_bytes(value: BaseModel | Mapping[str, Any]) -> bytes:
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else dict(value)
    return json.dumps(
        data,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def deserialize_verified(  # noqa: UP047 - package remains Python 3.11 compatible
    payload: bytes,
    expected_sha256: str,
    validator: Callable[[Any], ArtifactModel],
) -> ArtifactModel:
    if sha256_hex(payload) != expected_sha256:
        raise StateConflictError("artifact digest does not match stored bytes")
    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StateConflictError("artifact is not valid JSON") from error
    if not isinstance(decoded, dict):
        raise StateConflictError("artifact root must be a JSON object")
    try:
        return validator(decoded)
    except (TypeError, ValueError) as error:
        raise StateConflictError("artifact does not match its canonical schema") from error
