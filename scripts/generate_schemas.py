#!/usr/bin/env python3
"""Generate JSON Schema files and TypeScript types from canonical Pydantic models.

Outputs:
  contracts/schemas/*.schema.json
  contracts/generated/contracts.ts
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

root = Path(__file__).resolve().parents[1]
backend_path = root / "backend"
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

from coldchain.contracts.schemas import (  # noqa: E402
    CreateRunRequest,
    DemoRunSummary,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    Report,
    Review,
    ReviewRequest,
    Run,
    RunResponse,
    RunSummary,
    Snapshot,
)


def generate_json_schemas() -> None:
    schemas_dir = root / "contracts" / "schemas"
    schemas_dir.mkdir(parents=True, exist_ok=True)

    models = [
        ("snapshot.schema.json", Snapshot),
        ("report.schema.json", Report),
        ("run.schema.json", Run),
        ("review.schema.json", Review),
    ]

    for filename, model_cls in models:
        target = schemas_dir / filename
        schema = model_cls.model_json_schema(mode="serialization")
        target.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
        print(f"  [OK] Generated {target.relative_to(root)}")


def _generate_typescript_via_inspection(models: list[type], combined: dict[str, Any]) -> str:
    """Deterministic fallback that generates TypeScript interfaces from schema definitions."""
    lines: list[str] = [
        "export const SCHEMA_VERSION = \"1.0\" as const;",
        "",
        "export type SchemaVersion = typeof SCHEMA_VERSION;",
        "export type UUID = string;",
        "export type ISODateTime = string;",
        "",
    ]

    defs: dict[str, Any] = combined.get("$defs", {})

    # Emit enum types first
    for name, definition in sorted(defs.items()):
        if "enum" in definition:
            variants = " | ".join(json.dumps(v) for v in definition["enum"])
            lines.append(f"export type {name} = {variants};")

    lines.append("")

    # Helper to map a property schema to a TypeScript type
    def type_str(prop: dict[str, Any]) -> str:
        if "$ref" in prop:
            ref_name = prop["$ref"].split("/")[-1]
            return ref_name
        if "anyOf" in prop:
            subtypes = []
            for sub in prop["anyOf"]:
                if sub.get("type") == "null":
                    subtypes.append("null")
                elif "$ref" in sub:
                    subtypes.append(sub["$ref"].split("/")[-1])
                elif "type" in sub:
                    subtypes.append("string" if sub["type"] == "string" else ("number" if sub["type"] in ("integer", "number") else "boolean"))
            return " | ".join(subtypes)
        ptype = prop.get("type")
        if ptype == "string":
            if "const" in prop:
                return json.dumps(prop["const"])
            return "string"
        if ptype in ("integer", "number"):
            return "number"
        if ptype == "boolean":
            return "boolean"
        if ptype == "array":
            item_type = type_str(prop.get("items", {}))
            return f"{item_type}[]"
        return "unknown"

    # Emit object interfaces
    for name, definition in sorted(defs.items()):
        if definition.get("type") == "object" and "properties" in definition:
            lines.append(f"export interface {name} {{")
            required = set(definition.get("required", []))
            for prop_name, prop_schema in definition["properties"].items():
                opt = "" if prop_name in required else "?"
                t = type_str(prop_schema)
                lines.append(f"  {prop_name}{opt}: {t};")
            lines.append("}")
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def generate_typescript_types() -> None:
    target_dir = root / "contracts" / "generated"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "contracts.ts"

    models = [
        Snapshot,
        Report,
        Run,
        Review,
        RunSummary,
        DemoRunSummary,
        CreateRunRequest,
        RunResponse,
        ErrorResponse,
        HealthResponse,
        ReviewRequest,
    ]

    combined: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ColdChainContracts",
        "type": "object",
        "properties": {
            m.__name__: {"$ref": f"#/$defs/{m.__name__}"} for m in models
        },
        "$defs": {},
    }
    for m in models:
        schema = m.model_json_schema(mode="serialization")
        combined["$defs"][m.__name__] = {k: v for k, v in schema.items() if k != "$defs"}
        if "$defs" in schema:
            for k, v in schema["$defs"].items():
                combined["$defs"][k] = v

    output_ts: str | None = None
    try:
        proc = subprocess.run(
            ["npx", "--yes", "json-schema-to-typescript", "--unreachableDefinitions"],
            input=json.dumps(combined),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            header = (
                "export const SCHEMA_VERSION = \"1.0\" as const;\n"
                "export type UUID = string;\n"
                "export type ISODateTime = string;\n\n"
            )
            raw = proc.stdout
            lines = raw.splitlines()
            filtered_lines = []
            skip = False
            for line in lines:
                if line.startswith("export interface ColdChainContracts {"):
                    skip = True
                    continue
                if skip:
                    if line.startswith("}"):
                        skip = False
                    continue
                filtered_lines.append(line)
            output_ts = header + "\n".join(filtered_lines).strip() + "\n"
    except Exception as exc:
        print(f"  Note: npx json-schema-to-typescript unavailable ({exc}); using deterministic inspection", file=sys.stderr)

    if output_ts is None:
        output_ts = _generate_typescript_via_inspection(models, combined)

    target.write_text(output_ts, encoding="utf-8")
    print(f"  [OK] Generated {target.relative_to(root)}")


def main() -> int:
    print("Generating contract JSON Schemas (mode=serialization)...")
    generate_json_schemas()
    print("Generating TypeScript types...")
    generate_typescript_types()
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
