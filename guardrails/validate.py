#!/usr/bin/env python3
"""Validate one workflow artifact against its JSON schema.

Usage:
    python3 guardrails/validate.py <schema-name> <artifact.json|->
    python3 guardrails/validate.py --list
"""

import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("jsonschema is required: pip install jsonschema\n")
    sys.exit(2)

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


def load_schema(name):
    path = SCHEMA_DIR / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"unknown schema {name!r}: {path} does not exist")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2

    if argv[0] == "--list":
        for path in sorted(SCHEMA_DIR.glob("*.json")):
            print(path.stem)
        return 0

    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    name, target = argv
    schema = load_schema(name)
    try:
        raw = sys.stdin.read() if target == "-" else Path(target).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"FAIL {name}: cannot read {target}: {exc}", file=sys.stderr)
        return 1

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"FAIL {name}: not valid JSON: {exc}", file=sys.stderr)
        return 1

    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        print(f"FAIL {name}: {len(errors)} schema violation(s)", file=sys.stderr)
        for err in errors:
            where = ".".join(str(p) for p in err.absolute_path) or "<root>"
            print(f"  - {where}: {err.message}", file=sys.stderr)
        return 1

    print(f"OK {name}: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
