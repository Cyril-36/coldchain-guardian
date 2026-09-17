"""Canonical JSON serialization and digest verification."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from .exceptions import ArtifactDigestMismatch

ArtifactValidator = Callable[[Mapping[str, Any]], dict[str, Any]]


def copy_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Default validator used until a canonical report model is published."""

    return deepcopy(dict(value))


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Serialize deterministic UTF-8 JSON, rejecting NaN and non-JSON values."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def deserialize_verified(
    payload: bytes,
    expected_sha256: str,
    validator: ArtifactValidator,
) -> dict[str, Any]:
    """Verify byte identity before deserializing and validating an artifact."""

    if sha256_hex(payload) != expected_sha256:
        raise ArtifactDigestMismatch("artifact digest does not match stored bytes")
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("artifact root must be a JSON object")
    return validator(decoded)
