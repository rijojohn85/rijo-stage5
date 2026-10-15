#!/usr/bin/env python3
"""Roll every retained workflow eval run into one success-metrics CSV.

The certification asks for the end-to-end success rate as monitoring evidence. Prose in a
chat is not evidence; a CSV regenerated from the result files is, because anyone can re-run
this script and get the same table.

Reads results/workflow*.json (the workflow-level eval, which chains the real prompts) and
writes results/success_metrics.csv with one row per case per run, then per-case and overall
summary rows.

Usage:
    python3 scripts/success_metrics.py
"""

import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS = REPO / "results"
OUT = RESULTS / "success_metrics.csv"

COLUMNS = [
    "run", "case", "result", "terminal_status", "route", "steps",
    "session_ids_recorded", "cost_usd", "failure_detail",
]


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def rows_from(path):
    data = load(path)
    if not data:
        return []
    rows = []
    for r in data.get("results", {}).get("results", []):
        case = ((r.get("testCase") or {}).get("description") or "").split(" ->")[0].strip()
        try:
            out = json.loads((r.get("response") or {}).get("output") or "null")
        except json.JSONDecodeError:
            out = None
        out = out or {}
        steps = out.get("steps") or []
        failure = next(
            (f"{s.get('step')}: {s.get('error')}" for s in steps if not s.get("ok")), ""
        )
        if not failure:
            for c in (r.get("gradingResult") or {}).get("componentResults") or []:
                if not c.get("pass"):
                    failure = (c.get("reason") or "")[:160]
                    break
        rows.append({
            "run": path.name,
            "case": case,
            "result": "pass" if r.get("success") else "fail",
            "terminal_status": out.get("status", ""),
            "route": out.get("route") or "",
            "steps": len(steps),
            "session_ids_recorded": sum(1 for s in steps if s.get("session_id")),
            "cost_usd": out.get("cost_usd", ""),
            "failure_detail": failure,
        })
    return rows


def main():
    files = sorted(RESULTS.glob("workflow*.json"))
    if not files:
        sys.stderr.write("no results/workflow*.json found\n")
        return 1

    rows = []
    for path in files:
        rows.extend(rows_from(path))

    if not rows:
        sys.stderr.write("no workflow results found\n")
        return 1

    cases = list(dict.fromkeys(r["case"] for r in rows))
    passed = sum(1 for r in rows if r["result"] == "pass")

    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

        writer.writerow({c: "" for c in COLUMNS})
        for case in cases:
            subset = [r for r in rows if r["case"] == case]
            ok = sum(1 for r in subset if r["result"] == "pass")
            writer.writerow({
                **{c: "" for c in COLUMNS},
                "run": "SUMMARY",
                "case": case,
                "result": f"{ok}/{len(subset)}",
                "terminal_status": f"{100 * ok / len(subset):.1f}%",
            })
        writer.writerow({
            **{c: "" for c in COLUMNS},
            "run": "SUMMARY",
            "case": "ALL",
            "result": f"{passed}/{len(rows)}",
            "terminal_status": f"{100 * passed / len(rows):.1f}%",
        })

    print(f"wrote {OUT}")
    print(f"  runs: {len(files)}   case-samples: {len(rows)}   passed: {passed}"
          f"   success rate: {100 * passed / len(rows):.1f}%")
    print(f"  total measured cost: ${sum(float(r['cost_usd'] or 0) for r in rows):.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
