"""The evaluated deterministic path and the deployed one must be one implementation.

The holdout number is only meaningful if the proposer it scores is the same object
the worker runs in AWS. A copied baseline could drift and make the eval describe
code that is not deployed.
"""

from __future__ import annotations

import eval.baseline as baseline
from coldchain.investigation import rule_proposer
from coldchain.worker import handler


def test_eval_baseline_is_the_production_rule_proposer() -> None:
    assert baseline.baseline_proposal is rule_proposer.rule_proposal


def test_worker_fallback_is_the_same_object() -> None:
    assert handler.rule_proposal is rule_proposer.rule_proposal


def test_rule_proposer_lives_in_the_deployed_package() -> None:
    # eval/ is not bundled into the Lambda; coldchain/ is. If the implementation
    # moved back out of the deployed package, the worker fallback would break in
    # AWS while every local test still passed.
    assert rule_proposer.__name__.startswith("coldchain.")
