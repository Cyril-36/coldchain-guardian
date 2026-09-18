"""Guards on the holdout set itself: frozen, label-free, and outside the bundle."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from coldchain.core.detector import build_detection_result
from eval.builder import POLICY
from eval.holdout import HOLDOUT

REPO_ROOT = Path(__file__).resolve().parents[2]

# Recompute with scripts/run_eval.py after any deliberate change, and say in the PR
# why the holdout moved. docs/VERIFICATION.md requires a frozen set; a silent edit
# during prompt tuning is exactly what this digest exists to prevent.
DATASET_DIGEST = "562c8ef6ee7746006cc6452ac2cebc52eb8a06c6868866e55fa3159bee2c3a9c"


def _dataset_digest() -> str:
    digest = hashlib.sha256()
    for case in sorted(HOLDOUT, key=lambda c: c.case_id):
        digest.update(
            json.dumps(case.snapshot().model_dump(mode="json"), sort_keys=True).encode()
        )
    return digest.hexdigest()


def test_holdout_is_frozen() -> None:
    assert _dataset_digest() == DATASET_DIGEST


def test_family_composition_matches_the_release_gate() -> None:
    assert Counter(case.family for case in HOLDOUT) == {
        "door": 4,
        "refrigeration": 4,
        "sensor": 4,
        "ambiguous": 4,
        "normal": 4,
    }


def test_snapshots_are_deterministic() -> None:
    for case in HOLDOUT:
        first = case.snapshot().model_dump(mode="json")
        second = case.snapshot().model_dump(mode="json")
        assert first == second, case.case_id


@pytest.mark.parametrize("case", HOLDOUT, ids=lambda c: c.case_id)
def test_no_label_reaches_the_snapshot(case) -> None:
    """A snapshot carries observations only: no expected answer, note or case id.

    The bare family name is deliberately not checked: "door" and "sensor" are also
    legitimate contract vocabulary (`door_state`, `sensor_id`), so matching on them
    would flag every snapshot. The case id carries the family and is checked instead,
    along with anything that would actually give the answer away.
    """
    serialized = json.dumps(case.snapshot().model_dump(mode="json")).lower()
    leaked = [
        token
        for token in (
            case.case_id,
            case.expected_outcome,
            case.expected_hypothesis,
            case.note,
        )
        if token and token.lower() in serialized
    ]
    assert not leaked, f"{case.case_id} leaks {leaked}"


@pytest.mark.parametrize("case", HOLDOUT, ids=lambda c: c.case_id)
def test_expected_excursion_state_matches_the_detector(case) -> None:
    """A normal case must really be in range, and every other case really out of it."""
    detection = build_detection_result(case.snapshot())
    if case.family == "normal":
        assert not detection.has_excursion
        assert not detection.needs_review
    else:
        assert detection.has_excursion


def test_every_case_uses_the_same_policy() -> None:
    """A differing policy would make agreement scores incomparable across cases."""
    for case in HOLDOUT:
        assert case.snapshot().policy == POLICY


def test_eval_package_sits_outside_the_lambda_bundle() -> None:
    """Expected answers must not be packaged into a deployed runtime."""
    template = (REPO_ROOT / "infra" / "template.yaml").read_text(encoding="utf-8")
    code_uris = {
        line.split("CodeUri:")[1].strip()
        for line in template.splitlines()
        if "CodeUri:" in line
    }
    assert code_uris, "template declares no CodeUri"
    assert code_uris == {"../backend/"}, code_uris
    assert not (REPO_ROOT / "backend" / "eval").exists()
