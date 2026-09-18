"""Validate all JSON contract examples against canonical Pydantic v2 schemas.

Exits with code 0 if all examples conform to schemas and constraints;
exits with code 1 and prints violations otherwise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

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
    instances: dict[str, Any] = {}

    for filename, model_cls in expected_files:
        path = examples_dir / filename
        if not path.is_file():
            errors.append(f"Missing required example: {filename}")
            continue

        try:
            content = path.read_text(encoding="utf-8")
            parsed = json.loads(content)
            instance = model_cls.model_validate(parsed)
            # Ensure serialization round-trip succeeds
            dumped = instance.model_dump(mode="json")
            assert isinstance(dumped, dict)
            instances[filename] = instance
            validated += 1
            print(f"  [OK] {filename} (validated {model_cls.__name__})")
        except Exception as exc:
            errors.append(f"Failed validating {filename} against {model_cls.__name__}: {exc}")

    # Cross-reference integrity checks for door-snapshot references
    door_snap = instances.get("door-snapshot.json")
    if door_snap is not None and isinstance(door_snap, Snapshot):
        expected_digest = snapshot_sha256(door_snap)
        print(f"\n  Checking door-snapshot.json integrity (sha256: {expected_digest})...")

        valid_event_ids: set[str] = {r.event_id for r in door_snap.readings} | {
            e.event_id for e in door_snap.events
        }

        for r_file in (
            "supported-report.json",
            "unresolved-report.json",
            "model-unavailable-report.json",
        ):
            report = instances.get(r_file)
            if report is not None and isinstance(report, Report):
                if report.snapshot_id != door_snap.snapshot_id:
                    errors.append(
                        f"{r_file} snapshot_id ({report.snapshot_id}) does not match "
                        f"door-snapshot.json snapshot_id ({door_snap.snapshot_id})"
                    )
                if report.snapshot_sha256 != expected_digest:
                    errors.append(
                        f"{r_file} snapshot_sha256 ({report.snapshot_sha256}) does not match "
                        f"computed door-snapshot.json digest ({expected_digest})"
                    )
                if not report.evidence:
                    errors.append(f"{r_file} must contain at least one evidence item")
                else:
                    invalid_record_ids = [
                        (ev.evidence_id, rec_id)
                        for ev in report.evidence
                        for rec_id in ev.record_ids
                        if rec_id not in valid_event_ids
                    ]
                    if invalid_record_ids:
                        for ev_id, rec_id in invalid_record_ids:
                            errors.append(
                                f"{r_file} evidence '{ev_id}' cites record_id '{rec_id}' "
                                f"not found in door-snapshot.json"
                            )
                    else:
                        print(
                            f"  [OK] {r_file} digest matches door-snapshot and includes "
                            f"{len(report.evidence)} evidence items with valid snapshot record_ids"
                        )

    if errors:
        print("\nValidation Errors:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    print(f"\nAll {validated} contract example files validated successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(validate_examples())
