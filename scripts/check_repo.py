"""Validate the planning repository without cloud credentials or extra packages."""

from pathlib import Path
import re
import sys

root = Path(__file__).resolve().parents[1]
required = [
    "README.md", "PLAN.md", "AGENTS.md", "AGENT.md",
    "docs/CONTRACTS.md", "docs/01-CYRIL.md", "docs/02-HARSHITH.md",
    "docs/03-NAVADEEP.md", "docs/TEAM-CLOUD-WORKFLOW.md",
    "docs/VERIFICATION.md", ".github/PULL_REQUEST_TEMPLATE.md",
]
issues = []
for relative in required:
    if not (root / relative).is_file():
        issues.append(f"Missing required file: {relative}")

for path in root.rglob("*.md"):
    if ".git" in path.parts:
        continue
    content = path.read_text(encoding="utf-8")
    if content.count("```") % 2:
        issues.append(f"Unbalanced code fence: {path.relative_to(root)}")
    for target in re.findall(r"\]\(([^)]+)\)", content):
        if "://" in target or target.startswith("#"):
            continue
        local_target = target.split("#", 1)[0]
        if local_target and not (path.parent / local_target).exists():
            issues.append(f"Broken link in {path.relative_to(root)}: {target}")

if issues:
    print("\n".join(issues), file=sys.stderr)
    raise SystemExit(1)
print("Planning repository checks passed.")
