#!/usr/bin/env python3
"""promptfoo python assertions for the workflow agents.

One entry point, `get_assert(output, context)`. The expected values come from the test's
`vars.expect` map, so every case states its own expectation next to its input.

Supported keys in `vars.expect`:
    schema              schema name under schemas/ (default "triage")
    classification      exact value required
    decision            exact value required
    escalation          exact value required
    root_cause_mentions substring the root_cause must contain (case-insensitive)
    files_include       every listed path must appear in `files` (or `changed`)
    files_exclude       no listed path may appear in `files` (or `changed`)
    test_mentions       substring the `test` value must contain (case-insensitive)
    test_passed         exact boolean required
    options_min         minimum length of the `options` array
    mentions            list of {field, contains} substrings the field must contain
    absent_keys         keys that must NOT be present in the output
"""

import json
import posixpath
from pathlib import Path

import jsonschema

REPO = Path(__file__).resolve().parents[1]
SCHEMA_DIR = REPO / "schemas"
EXACT_KEYS = ("classification", "decision", "escalation", "verdict")


def _schema(name):
    return json.loads((SCHEMA_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _norm(path):
    return posixpath.normpath(str(path).strip().replace("\\", "/"))


def _check_files(data, expect, problems):
    # `files` is the plan's allow-list; `changed` is what implement reports touching.
    files = data.get("changed") if isinstance(data.get("changed"), list) else data.get("files")
    if not isinstance(files, list):
        return
    present = {_norm(f) for f in files}

    for want in expect.get("files_include", []):
        if _norm(want) not in present:
            problems.append(f"files is missing {want!r}")
    for bad in expect.get("files_exclude", []):
        if _norm(bad) in present:
            problems.append(f"files must not include {bad!r}")


def _check_findings(data, expect, problems):
    findings = data.get("findings")
    if not isinstance(findings, list):
        return
    if "findings_max" in expect and len(findings) > expect["findings_max"]:
        details = "; ".join(
            f"{f.get('category')}: {str(f.get('detail'))[:60]}"
            for f in findings if isinstance(f, dict)
        )
        problems.append(
            f"{len(findings)} findings, expected at most {expect['findings_max']} ({details})"
        )
    categories = {str(f.get("category")) for f in findings if isinstance(f, dict)}
    for want in expect.get("categories_include", []):
        if want not in categories:
            problems.append(f"no finding with category {want!r} (got {sorted(categories)})")


def _check_extra(data, expect, problems):
    options = data.get("options")
    if isinstance(options, list) and "options_min" in expect and len(options) < expect["options_min"]:
        problems.append(
            f"options: {len(options)}, expected at least {expect['options_min']}"
        )

    for check in expect.get("mentions", []):
        field = str(check.get("field"))
        needle = str(check.get("contains", ""))
        if needle and needle.lower() not in str(data.get(field, "")).lower():
            problems.append(f"{field} does not mention {needle!r}")

    for key in expect.get("absent_keys", []):
        if key in data:
            problems.append(f"{key} must be absent: a human fills it in")


def get_assert(output, context):
    expect = (context.get("vars") or {}).get("expect") or {}
    problems = []

    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        return {"pass": False, "score": 0.0, "reason": f"output is not JSON: {exc}"}

    validator = jsonschema.Draft202012Validator(_schema(expect.get("schema", "triage")))
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path)):
        where = ".".join(str(p) for p in err.absolute_path) or "<root>"
        problems.append(f"schema {where}: {err.message}")

    for key in EXACT_KEYS:
        if key in expect and data.get(key) != expect[key]:
            problems.append(f"{key}: got {data.get(key)!r}, want {expect[key]!r}")

    needle = expect.get("root_cause_mentions")
    if needle and needle.lower() not in str(data.get("root_cause", "")).lower():
        problems.append(f"root_cause does not mention {needle!r}")

    _check_files(data, expect, problems)

    _check_findings(data, expect, problems)

    _check_extra(data, expect, problems)

    test_needle = expect.get("test_mentions")
    if test_needle and test_needle.lower() not in str(data.get("test", "")).lower():
        problems.append(f"test does not mention {test_needle!r}")

    if "test_passed" in expect and data.get("test_passed") != expect["test_passed"]:
        problems.append(
            f"test_passed: got {data.get('test_passed')!r}, want {expect['test_passed']!r}"
        )

    if not problems:
        return {"pass": True, "score": 1.0, "reason": "ok"}
    return {"pass": False, "score": 0.0, "reason": "; ".join(problems)}
