#!/usr/bin/env python3
"""Deterministic prompt-injection backstop for the screen agent.

Whether a bug report is trying to instruct an AI is a screening-model judgement, and judgements
can be fooled. This gate is the code backstop that cannot be talked out of it: if the report
text matches a trigger in `screen_rules.yaml`, the gate overrides the screen artifact to
`suspicious`, so the workflow stops for a human anyway. Its evidence is the matched report text,
so it stays an exact quote of the report.

Honest limit: it catches known attack patterns only. A paraphrase, another language or a novel
attack gets through the gate; the screening model and the review of it are the main defence.

Usage:
    python3 guardrails/screen_gate.py <report-file> <screen.json> [--apply]

Exit codes:
    0  no trigger matched; the screen artifact stands
    1  suspicious (a trigger matched, or the artifact already says so); the workflow must stop
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

RULES_PATH = Path(__file__).resolve().parent / "screen_rules.yaml"
MAX_EVIDENCE_ITEMS = 5      # schemas/screen.json: evidence maxItems
MAX_EVIDENCE_CHARS = 300    # schemas/screen.json: evidence item maxLength
SUSPICIOUS = "suspicious"


def load_triggers():
    rules = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8")) or {}
    return [
        (t["id"], t.get("note", ""), [re.compile(p) for p in t["all_of"]])
        for t in rules.get("triggers", [])
    ]


def matches(report_text, triggers):
    """(id, note, matched report text) for each trigger whose every pattern matches the report."""
    hits = []
    for tid, note, patterns in triggers:
        found = [p.search(report_text) for p in patterns]
        if all(found):
            hits.append((tid, note, [m.group(0) for m in found]))
    return hits


def merged_evidence(existing, quotes):
    """`existing` then `quotes`, trimmed to the schema's item length, deduplicated, at most 5 items."""
    merged = list(existing)
    for quote in quotes:
        item = quote.strip()[:MAX_EVIDENCE_CHARS].strip()
        if item and item not in merged:
            merged.append(item)
    return merged[:MAX_EVIDENCE_ITEMS]


def apply_override(artifact, hits):
    """Turn the screen artifact into a suspicious one whose evidence quotes the matched text."""
    quotes = [quote for _, _, texts in hits for quote in texts]
    ids = ", ".join(tid for tid, _, _ in hits)
    artifact["verdict"] = SUSPICIOUS
    artifact["evidence"] = merged_evidence(artifact.get("evidence", []), quotes)
    artifact["reason"] = f"{artifact.get('reason', '').rstrip()} [screen-gate override: {ids}]".strip()


def read_inputs(report_path, artifact_path):
    """(report text, artifact) or raises OSError / json.JSONDecodeError."""
    report_text = Path(report_path).read_text(encoding="utf-8")
    artifact = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
    return report_text, artifact


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2

    apply_change = "--apply" in argv
    positional = [a for a in argv if not a.startswith("--")]
    if len(positional) != 2 or argv[0].startswith("--"):
        print(__doc__.strip(), file=sys.stderr)
        return 2

    report_path, artifact_path = positional
    try:
        report_text, artifact = read_inputs(report_path, artifact_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"screen-gate: cannot read input: {exc}", file=sys.stderr)
        return 2

    if artifact.get("verdict") == SUSPICIOUS:
        print("OK screen-gate: the screen already rated the report suspicious; no override needed")
        return 1

    hits = matches(report_text, load_triggers())
    if not hits:
        print("OK screen-gate: no trigger matched; the screen artifact stands")
        return 0

    if apply_change:
        apply_override(artifact, hits)
        Path(artifact_path).write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    print(f"BLOCKED screen-gate: {', '.join(tid for tid, _, _ in hits)}", file=sys.stderr)
    for tid, note, _ in hits:
        print(f"  - {tid}: {note}", file=sys.stderr)
    if apply_change:
        print("  applied: verdict=suspicious in " + artifact_path, file=sys.stderr)
    else:
        print("  dry run: re-run with --apply to write the override", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
