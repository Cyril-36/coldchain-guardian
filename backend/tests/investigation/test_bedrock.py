"""SDK boundary and hard invocation/tool budgets without using AWS."""

import contextlib
import threading
import time
from types import SimpleNamespace

import pytest

from coldchain.investigation.bedrock import MAX_MODEL_CALLS, MAX_TOOL_CALLS, _Budget


def test_budget_stops_model_and_tool_calls_at_limits() -> None:
    signal = threading.Event()
    budget = _Budget(time.monotonic() + 30, signal)

    for _ in range(MAX_MODEL_CALLS):
        event = SimpleNamespace(cancel=False)
        budget.before_model(event)
        assert event.cancel is False
    extra = SimpleNamespace(cancel=False)
    budget.before_model(extra)
    assert extra.cancel and budget.model_calls == MAX_MODEL_CALLS
    assert signal.is_set()

    signal.clear()
    budget = _Budget(time.monotonic() + 30, signal)
    for _ in range(MAX_TOOL_CALLS):
        event = SimpleNamespace(cancel_tool=False)
        budget.before_tool(event)
        assert event.cancel_tool is False
    extra_tool = SimpleNamespace(cancel_tool=False)
    budget.before_tool(extra_tool)
    assert extra_tool.cancel_tool and budget.tool_calls == MAX_TOOL_CALLS
    assert signal.is_set()


def test_expired_deadline_cancels_before_model_call() -> None:
    signal = threading.Event()
    budget = _Budget(time.monotonic() - 1, signal)
    event = SimpleNamespace(cancel=False)

    budget.before_model(event)

    assert event.cancel and signal.is_set()
    assert budget.model_calls == 0


def test_installed_sdk_accepts_actual_tool_and_structured_output_api(monkeypatch) -> None:
    strands = pytest.importorskip("strands")
    models = pytest.importorskip("strands.models")
    from datetime import UTC, datetime

    from strands.hooks import BeforeModelCallEvent, BeforeToolCallEvent

    from coldchain.contracts import Snapshot
    from coldchain.investigation.bedrock import BedrockProposer
    from coldchain.investigation.tools import ToolContext
    from coldchain.simulator import generate_snapshot

    snapshot = Snapshot.model_validate(
        generate_snapshot("door_exposure", 17, datetime(2026, 9, 18, tzinfo=UTC))
    )
    ctx = ToolContext(snapshot, run_id="71ccce3f-168a-4f96-b3df-60eaa9062d6e")
    seen: dict = {}

    class FakeBedrockModel:
        def __init__(self, **kwargs):
            seen["model"] = kwargs

    class FakeAgent:
        def __init__(self, **kwargs):
            seen["tools"] = [item.tool_name for item in kwargs["tools"]]
            seen["callback"] = kwargs["callback_handler"]
            seen["retry"] = kwargs["retry_strategy"]
            self.hooks = []

        def add_hook(self, callback, event_type):
            self.hooks.append((callback, event_type))

        def __call__(self, prompt, **kwargs):
            seen["prompt"] = prompt
            seen["limits"] = kwargs["limits"]
            for callback, event_type in self.hooks:
                if event_type == BeforeModelCallEvent:
                    callback(SimpleNamespace(cancel=False))
                if event_type == BeforeToolCallEvent:
                    callback(SimpleNamespace(cancel_tool=False))
            return SimpleNamespace(
                structured_output=kwargs["structured_output_model"](outcome="unresolved")
            )

    monkeypatch.setattr(strands, "Agent", FakeAgent)
    monkeypatch.setattr(models, "BedrockModel", FakeBedrockModel)

    proposal = BedrockProposer("test-model", "us-east-1")(ctx)

    assert proposal.outcome == "unresolved"
    assert len(seen["tools"]) == 6
    assert "get_door_events" in seen["tools"]
    assert seen["model"]["region_name"] == "us-east-1"
    assert seen["callback"] is None and seen["retry"] is None
    assert seen["limits"]["turns"] == MAX_MODEL_CALLS
    assert "scenario_id" not in seen["prompt"]


def test_failed_invocation_does_not_reuse_the_previous_calls_statistics() -> None:
    """Stats must describe the call that just happened, or be zero.

    The eval reports token usage and request counts per case. If a failed invocation
    left the previous call's totals in place, the wrapper would add them a second
    time and the reported cost would be silently inflated.
    """
    from coldchain.investigation.bedrock import BedrockProposer, InvocationStats

    proposer = BedrockProposer("some-model", "us-east-1")
    proposer.last_stats = InvocationStats(
        model_calls=5, tool_calls=9, input_tokens=1200, output_tokens=300
    )

    proposer.deadline = 0.0  # already expired, so the call cannot proceed
    # The exact failure depends on the environment (expired deadline, missing
    # credentials); what matters is that any failure leaves the stats reset.
    with contextlib.suppress(Exception):
        proposer(object())

    assert proposer.last_stats == InvocationStats()
