#!/usr/bin/env python3
"""Check that every changed path is one the plan allows.

This is the guard between /plan and /implement: the plan declares the files the fix may
touch, and the implement step must not step outside that list.

Usage:
    python3 guardrails/plan_scope.py <plan.json> <changed-path> [<changed-path> ...]
    printf '%s\\n' <path> ... | python3 guardrails/plan_scope.py <plan.json> --stdin
    python3 guardrails/plan_scope.py <plan.json> --artifact <03-implement.json>

Exit 0 when every changed path is allowed, 1 when any path is not, 2 on a usage error.
"""

import json
import posixpath
import sys
from pathlib import Path


def normalize(path):
    """Canonical form for comparison: forward slashes, no leading './', normpath'd."""
    value = str(path).strip().replace("\\", "/")
    if not value:
        return None
    return posixpath.normpath(value)


def load_json(path, what):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"plan-scope: cannot read {what} {path}: {exc}", file=sys.stderr)
        return None


def main(argv):
    if len(argv) < 2 or argv[0] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr)
        return 2

    plan_path, rest = argv[0], argv[1:]
    plan = load_json(plan_path, "plan")
    if plan is None:
        return 2

    if rest[:1] == ["--artifact"]:
        if len(rest) != 2:
            print("plan-scope: --artifact needs exactly one path", file=sys.stderr)
            return 2
        artifact = load_json(rest[1], "artifact")
        if artifact is None:
            return 2
        changed = artifact.get("changed") or []
    elif rest == ["--stdin"]:
        changed = sys.stdin.read().splitlines()
    else:
        changed = rest

    allowed = {normalize(f) for f in plan.get("files", []) if normalize(f)}
    if not allowed:
        print("plan-scope: plan names no files", file=sys.stderr)
        return 2

    violations, seen = [], 0
    for raw in changed:
        path = normalize(raw)
        if not path:
            continue
        seen += 1
        if path not in allowed:
            violations.append(path)

    if violations:
        print(f"FAIL plan-scope: {len(violations)} path(s) outside the plan", file=sys.stderr)
        for path in violations:
            print(f"  - {path} (allowed: {', '.join(sorted(allowed))})", file=sys.stderr)
        return 1

    print(f"OK plan-scope: {seen} changed path(s) within the plan")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
