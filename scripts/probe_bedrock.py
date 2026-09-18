#!/usr/bin/env python3
"""One bounded Bedrock probe: a real tool call plus a validated structured result.

docs/01-CYRIL.md requires proving the chosen model can call a tool and return a
schema-valid object from the deployment identity before the investigator is trusted.
A plain "hello" response is not sufficient evidence.

This makes one bounded agent invocation with a small token budget. It prints the
model ID, region, package versions and latency, and never prints the prompt, the
model's free text, or any credential.
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import os
import sys
import time

from pydantic import BaseModel, ConfigDict, ValidationError


class ProbeResult(BaseModel):
    """Minimal structured output: forces schema-shaped generation, not prose."""

    model_config = ConfigDict(extra="forbid")

    tool_value: str
    tool_was_called: bool


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--max-tokens", type=int, default=256)
    args = parser.parse_args()

    # A browser `aws login` session writes only `login_session` into ~/.aws/config,
    # with no region, so botocore raises NoRegionError even when region_name is passed
    # to the client. Setting these makes the command self-sufficient.
    os.environ.setdefault("AWS_REGION", args.region)
    os.environ.setdefault("AWS_DEFAULT_REGION", args.region)

    from strands import Agent, tool
    from strands.models import BedrockModel

    calls: list[str] = []

    @tool(name="get_probe_token")
    def get_probe_token() -> str:
        """Return the probe token that must appear in the structured result."""
        calls.append("get_probe_token")
        return "coldchain-probe-ok"

    agent = Agent(
        model=BedrockModel(
            model_id=args.model_id,
            region_name=args.region,
            max_tokens=args.max_tokens,
        ),
        tools=[get_probe_token],
        system_prompt=(
            "Call get_probe_token exactly once, then return its value as tool_value "
            "and set tool_was_called to true."
        ),
        callback_handler=None,
    )

    started = time.monotonic()
    try:
        result = agent(
            "Call the tool and report its value.",
            structured_output_model=ProbeResult,
            limits={"turns": 4, "output_tokens": 1024, "total_tokens": 8000},
        )
    except Exception as exc:  # noqa: BLE001 - the probe reports failure, it does not raise
        # Report the failure class honestly; access and region failures look
        # different from model-quality failures and must not be conflated.
        print(
            json.dumps(
                {
                    "ok": False,
                    "failure": type(exc).__name__,
                    "detail": str(exc)[:300],
                    "model_id": args.model_id,
                    "region": args.region,
                },
                indent=2,
            )
        )
        return 1
    latency_s = round(time.monotonic() - started, 2)

    try:
        structured = ProbeResult.model_validate(result.structured_output)
    except ValidationError:
        print(json.dumps({"ok": False, "failure": "structured_output_invalid", "model_id": args.model_id, "region": args.region}))
        return 1
    ok = calls == ["get_probe_token"] and structured.tool_was_called and structured.tool_value == "coldchain-probe-ok"
    usage = getattr(getattr(result, "metrics", None), "accumulated_usage", None) or {}

    print(
        json.dumps(
            {
                "ok": ok,
                "model_id": args.model_id,
                "region": args.region,
                "tool_calls": calls,
                "structured_output_valid": True,
                "structured_output": structured.model_dump(),
                "latency_seconds": latency_s,
                "stop_reason": getattr(result, "stop_reason", None),
                "input_tokens": usage.get("inputTokens"),
                "output_tokens": usage.get("outputTokens"),
                "packages": {
                    name: md.version(name)
                    for name in ("strands-agents", "boto3", "botocore", "pydantic")
                },
            },
            indent=2,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
