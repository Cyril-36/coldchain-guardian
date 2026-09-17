"""Reproducible test validating all 8 contract example fixtures against
actual dashboard Zod client schemas.

Source: apps/web/src/api/client.ts
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

root = Path(__file__).resolve().parents[3]
verify_script = root / "scripts" / "verify_client_fixtures.js"


def test_all_eight_fixtures_pass_dashboard_zod_client_validation() -> None:
    """All 8 contract examples must strictly pass Navadeep's actual dashboard client schemas."""
    assert verify_script.is_file(), f"Verification script missing at {verify_script}"

    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js is not installed on this system.")

    proc = subprocess.run(
        [node_bin, str(verify_script)],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )

    if proc.returncode != 0:
        pytest.fail(
            f"Dashboard Zod validation failed with code {proc.returncode}:\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )

    assert "All 8 fixtures passed dashboard Zod client validation cleanly." in proc.stdout
