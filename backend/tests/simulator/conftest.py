from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from coldchain.simulator import generate_snapshot

BASE_TIMESTAMP = datetime(2026, 9, 17, 8, 0, tzinfo=UTC)


@pytest.fixture
def normal_snapshot() -> dict:
    return generate_snapshot("normal_control", 41, BASE_TIMESTAMP)


@pytest.fixture
def mutable_normal_snapshot(normal_snapshot: dict) -> dict:
    return deepcopy(normal_snapshot)
