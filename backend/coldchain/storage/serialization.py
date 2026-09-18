"""Canonical Pydantic artifact serialization and digest verification."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from pydantic import BaseModel

from .exceptions import StateConflictError

ArtifactModel = TypeVar("ArtifactModel", bound=BaseModel)
ArtifactValidator = Callable[[Any], BaseModel]


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
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("artifact root must be a JSON object")
    return validator(decoded)
