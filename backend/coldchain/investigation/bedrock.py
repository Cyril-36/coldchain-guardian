"""Bounded Strands/Bedrock adapter for a snapshot-bound investigator."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from coldchain.investigation.tools import (
    ToolContext,
    get_door_events,
    get_excursion_summary,
    get_handling_policy,
    get_refrigeration_events,
    get_sensor_comparison,
    get_vehicle_events,
)
from coldchain.investigation.verifier import InvestigationProposal


class StructuredOutputError(RuntimeError):
    """The model answered but the answer did not satisfy the proposal schema.

    Distinct from access, timeout and budget failures: those say nothing about output
    quality, and counting them together would overstate the schema failure rate.
    """


@dataclass
class InvocationStats:
    """What one proposal actually cost, measured rather than inferred."""

    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


MAX_MODEL_CALLS = 8
MAX_TOOL_CALLS = 12
MAX_SECONDS = 90

ToolEvent = Callable[[str, datetime, datetime, list[str]], None]


class _Budget:
    def __init__(self, deadline: float, cancel_signal: threading.Event) -> None:
        self.deadline = deadline
        self.cancel_signal = cancel_signal
        self.model_calls = 0
        self.tool_calls = 0
        self.exceeded = False

    def before_model(self, event: Any) -> None:
        if time.monotonic() >= self.deadline or self.model_calls >= MAX_MODEL_CALLS:
            self.exceeded = True
            self.cancel_signal.set()
            event.cancel = "Investigation budget exhausted"
            return
        self.model_calls += 1

    def before_tool(self, event: Any) -> None:
        if time.monotonic() >= self.deadline or self.tool_calls >= MAX_TOOL_CALLS:
            self.exceeded = True
            self.cancel_signal.set()
            event.cancel_tool = "Investigation budget exhausted"
            return
        self.tool_calls += 1


def _result_evidence_ids(result: dict[str, Any]) -> list[str]:
    """Record IDs actually returned to the model, capped for the visible trace."""
    found: list[str] = []

    def visit(value: Any) -> None:
        if len(found) >= 20:
            return
        if isinstance(value, dict):
            evidence_id = value.get("evidence_id")
            if isinstance(evidence_id, str) and evidence_id not in found:
                found.append(evidence_id)
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(result)
    return found


class BedrockProposer:
    """One SDK invocation with six read-only tools and explicit hard budgets."""

    def __init__(
        self,
        model_id: str,
        region_name: str,
        *,
        deadline: float | None = None,
        on_tool: ToolEvent | None = None,
    ) -> None:
        if not model_id or not region_name:
            raise ValueError("Bedrock model ID and AWS region are required")
        self.model_id = model_id
        self.region_name = region_name
        self.deadline = deadline
        self.on_tool = on_tool
        # Stats from the most recent __call__, for evaluation and cost reporting.
        self.last_stats = InvocationStats()

    def __call__(self, ctx: ToolContext) -> InvestigationProposal:
        from botocore.config import Config
        from strands import Agent, tool
        from strands.hooks import BeforeModelCallEvent, BeforeToolCallEvent
        from strands.models import BedrockModel

        deadline = self.deadline or time.monotonic() + MAX_SECONDS
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("investigation deadline expired")
        cancel_signal = threading.Event()
        budget = _Budget(deadline, cancel_signal)

        def bounded(
            name: str, operation: Callable[[ToolContext], dict[str, Any]]
        ) -> dict[str, Any]:
            started = datetime.now(UTC)
            result = operation(ctx)
            finished = datetime.now(UTC)
            if self.on_tool:
                self.on_tool(name, started, finished, _result_evidence_ids(result))
            return result

        @tool(name="get_excursion_summary")
        def excursion_summary() -> dict[str, Any]:
            """Read verified per-sensor excursion metrics and their evidence IDs."""
            return bounded("get_excursion_summary", get_excursion_summary)

        @tool(name="get_sensor_comparison")
        def sensor_comparison() -> dict[str, Any]:
            """Read time-aligned sensor differences and missing alignment information."""
            return bounded("get_sensor_comparison", get_sensor_comparison)

        @tool(name="get_door_events")
        def door_events() -> dict[str, Any]:
            """Read observed door events and measured temporal overlap facts."""
            return bounded("get_door_events", get_door_events)

        @tool(name="get_refrigeration_events")
        def refrigeration_events() -> dict[str, Any]:
            """Read observed refrigeration status events, including faults and unknowns."""
            return bounded("get_refrigeration_events", get_refrigeration_events)

        @tool(name="get_vehicle_events")
        def vehicle_events() -> dict[str, Any]:
            """Read observed vehicle movement context without inferring a cause."""
            return bounded("get_vehicle_events", get_vehicle_events)

        @tool(name="get_handling_policy")
        def handling_policy() -> dict[str, Any]:
            """Read the snapshot's configured temperature policy and evidence ID."""
            return bounded("get_handling_policy", get_handling_policy)

        model = BedrockModel(
            model_id=self.model_id,
            region_name=self.region_name,
            max_tokens=512,
            boto_client_config=Config(
                connect_timeout=5,
                read_timeout=min(30, max(1, int(remaining))),
                retries={"max_attempts": 1},
            ),
        )
        agent = Agent(
            model=model,
            tools=[
                excursion_summary,
                sensor_comparison,
                door_events,
                refrigeration_events,
                vehicle_events,
                handling_policy,
            ],
            system_prompt=(
                "Investigate only this synthetic snapshot. Call relevant evidence tools first; "
                "compare door exposure, refrigeration faults and sensor disagreement. "
                "Select only tool-returned evidence IDs. When evidence is missing, conflicting "
                "or cannot separate explanations, return unresolved. Never decide shipment "
                "release, disposal, product safety, a probability, or numerical measurements. "
                "Your output is typed evidence selection, not narrative text."
            ),
            callback_handler=None,
            retry_strategy=None,
        )
        agent.add_hook(budget.before_model, BeforeModelCallEvent)
        agent.add_hook(budget.before_tool, BeforeToolCallEvent)
        prompt = json.dumps(
            {
                "run_id": ctx.run_id,
                "snapshot_id": ctx.snapshot_id,
                "cutoff_at": ctx.cutoff_at.isoformat(),
                "anomaly_summary": get_excursion_summary(ctx),
            },
            separators=(",", ":"),
        )
        timer = threading.Timer(remaining, cancel_signal.set)
        timer.daemon = True
        timer.start()
        try:
            result = agent(
                prompt,
                structured_output_model=InvestigationProposal,
                limits={"turns": MAX_MODEL_CALLS, "output_tokens": 4096, "total_tokens": 12000},
                cancel_signal=cancel_signal,
            )
        finally:
            timer.cancel()
        usage = getattr(getattr(result, "metrics", None), "accumulated_usage", None) or {}
        self.last_stats = InvocationStats(
            model_calls=budget.model_calls,
            tool_calls=budget.tool_calls,
            input_tokens=int(usage.get("inputTokens") or 0),
            output_tokens=int(usage.get("outputTokens") or 0),
        )
        if budget.exceeded or cancel_signal.is_set() or budget.tool_calls == 0:
            raise RuntimeError("investigation ended without required evidence within budget")
        try:
            return InvestigationProposal.model_validate(result.structured_output)
        except ValidationError as exc:
            raise StructuredOutputError(str(exc)) from exc
