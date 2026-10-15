#!/usr/bin/env python3
"""Deterministic escalation backstop.

Whether a report needs a human is a triage-agent judgement, and judgements can be wrong.
This gate is the code backstop that cannot be talked out of it: if the report text matches a
trigger in `escalate_rules.yaml` and the triage artifact says `escalation: false`, the gate
overrides the artifact so the workflow stops for a human anyway.

This mirrors `D3_FRAUD_NEVER_AUTO` in the reference passing submission
(`/home/rijojohn/Downloads/akash-garg-stage4-submission-final`): a rule the model is told to
follow but that is re-enforced in code afterwards rather than trusted.

Usage:
    python3 guardrails/escalate_gate.py <report-file> <triage.json> [--apply]
    python3 guardrails/escalate_gate.py --self-test

Exit codes:
    0  no trigger matched; the workflow may proceed
    1  escalation enforced (or already set); the workflow must stop for a human
    2  usage or input error
"""

import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guard
    sys.stderr.write("pyyaml is required\n")
    sys.exit(2)

RULES_PATH = Path(__file__).resolve().parent / "escalate_rules.yaml"


def load_triggers():
    rules = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8")) or {}
    return [
        (t["id"], t.get("note", ""), re.compile(t["pattern"]))
        for t in rules.get("triggers", [])
    ]


def matches(report_text, report_path, triggers):
    """Triggers matching the report text, or its path (the report may be a filename)."""
    haystack = f"{report_text}\n{report_path or ''}"
    return [(tid, note) for tid, note, pattern in triggers if pattern.search(haystack)]


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2

    if argv[0] == "--self-test":
        return self_test()

    apply_change = "--apply" in argv
    positional = [a for a in argv if not a.startswith("--")]
    if len(positional) != 2 or (argv[0].startswith("--")):
        print(__doc__.strip(), file=sys.stderr)
        return 2

    report_path, artifact_path = positional
    try:
        report_text = Path(report_path).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"escalate-gate: cannot read report {report_path}: {exc}", file=sys.stderr)
        return 2
    try:
        artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"escalate-gate: cannot read artifact {artifact_path}: {exc}", file=sys.stderr)
        return 2

    if artifact.get("escalation") is True:
        print("OK escalate-gate: triage already escalated; no override needed")
        return 1

    hits = matches(report_text, report_path, load_triggers())
    if not hits:
        print("OK escalate-gate: no trigger matched; the workflow may proceed")
        return 0

    ids = ", ".join(tid for tid, _ in hits)
    notes = "; ".join(f"{tid}: {note}" for tid, note in hits)
    if apply_change:
        artifact["escalation"] = True
        artifact["decision"] = "defer"
        artifact["rationale"] = (
            f"{artifact.get('rationale', '').rstrip()} "
            f"[escalate-gate override: {notes}]"
        ).strip()
        Path(artifact_path).write_text(
            json.dumps(artifact, indent=2) + "\n", encoding="utf-8",
        )

    print(f"BLOCKED escalate-gate: {ids}", file=sys.stderr)
    for tid, note in hits:
        print(f"  - {tid}: {note}", file=sys.stderr)
    if apply_change:
        print(
            "  applied: escalation=true, decision=defer in " + artifact_path,
            file=sys.stderr,
        )
    else:
        print("  dry run: re-run with --apply to write the override", file=sys.stderr)
    return 1


def self_test():
    """Bypass test: something upstream forces a data-loss fix straight through.

    This is the attack the cert asks for. A fabricated triage artifact claims escalation is
    false and the fix can proceed. The gate must block it without any model call.
    """
    import tempfile

    report = (
        "save_records destroys the file when a write fails. We have lost customer records "
        "twice this week in production.\n"
    )
    fabricated = {
        "classification": "bug",
        "decision": "fix-now",
        "root_cause": "storage.py:save_records truncates before serializing",
        "rationale": "Looks like a small local fix; proceeding automatically.",
        "escalation": False,
    }

    print("=" * 70)
    print("BYPASS TEST: an upstream step fabricates a triage artifact claiming")
    print("escalation=false so a data-loss fix proceeds without a human")
    print("=" * 70)
    print("\nBEFORE gate:")
    print(f"  escalation = {fabricated['escalation']}")
    print(f"  decision   = {fabricated['decision']!r}")

    with tempfile.TemporaryDirectory() as tmp:
        rp = Path(tmp) / "report.md"
        ap = Path(tmp) / "01-triage.json"
        rp.write_text(report, encoding="utf-8")
        ap.write_text(json.dumps(fabricated), encoding="utf-8")
        rc = main([str(rp), str(ap), "--apply"])
        after = json.loads(ap.read_text(encoding="utf-8"))

    print("\nAFTER gate (guardrails/escalate_gate.py):")
    print(f"  escalation = {after['escalation']}")
    print(f"  decision   = {after['decision']!r}")
    print(f"  rationale  = {after['rationale'][:120]}")

    blocked = rc == 1 and after["escalation"] is True and after["decision"] == "defer"
    print(f"\nBYPASS BLOCKED: {blocked}")
    print("=" * 70)
    return 0 if blocked else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
