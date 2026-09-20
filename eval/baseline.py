"""The rule baseline the holdout is scored against.

Re-exported from production so the evaluated deterministic path and the deployed
deterministic path are the same object, not two copies that can drift.
"""

from __future__ import annotations

from coldchain.investigation.rule_proposer import rule_proposal

baseline_proposal = rule_proposal

__all__ = ["baseline_proposal", "rule_proposal"]
