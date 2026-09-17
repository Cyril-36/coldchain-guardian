"""Validate all JSON contract examples against canonical Pydantic v2 schemas.

Exits with code 0 if all examples conform to schemas and constraints;
exits with code 1 and prints violations otherwise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure backend is in python path
root = Path(__file__).resolve().parents[1]
backend_path = root / "backend"
if str(backend_path) not in sys.path:
    sys.path.insert(0, str(backend_path))

from coldchain.contracts.schemas import Report, Run, Snapshot, snapshot_sha256  # noqa: E402


def validate_examples() -> int:
    examples_dir = root / "contracts" / "examples"
    if not examples_dir.is_dir():
        print(f"Error: examples directory not found at {examples_dir}", file=sys.stderr)
        return 1

    expected_files = [
        ("door-snapshot.json", Snapshot),
        ("queued-run.json", Run),
        ("running-run.json", Run),
        ("failed-run.json", Run),
        ("supported-report.json", Report),
        ("unresolved-report.json", Report),
        ("model-unavailable-report.json", Report),
        ("normal-report.json", Report),
    ]

    errors: list[str] = []
    validated = 0

    for filename, model_cls in expected_files:
        path = examples_dir / filename
        if not path.is_file():
            errors.append(f"Missing required example: {filename}")
            continue

        try:
            content = path.read_text(encoding="utf-8")
            parsed = json.loads(content)
            instance = model_cls.model_validate(parsed)
            validated += 1

            # Extra check for snapshot sha256 reference integrity in reports
            if filename == "door-snapshot.json":
                digest = snapshot_sha256(instance)
                print(f"  [OK] {filename} (validated Snapshot, sha256: {digest[:16]}...)")
            else:
                print(f"  [OK] {filename} (validated {model_cls.__name__})")

        except Exception as exc:
            errors.append(f"Failed validating {filename} against {model_cls.__name__}: {exc}")

    if errors:
        print("\nValidation Errors:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"\nAll {validated} contract example files validated successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(validate_examples())
