#!/usr/bin/env python3
"""Build the structured evidence files (plan section 8, item 6) from what the evals actually wrote.

    results/pytest.txt           the plain-test report (pytest is run unless --skip-pytest)
    results/success_metrics.csv  per-case and per-criterion pass rates rolled up from results/*.json
    results/audit-summary.json   tokens and calculated cost across every eval run found

Nothing here calls a model. Re-running it on the same files gives the same output, so anyone can
check a claim in the README against these files.

How the audit summary is computed. Every dispatch and every coordinator turn writes an audit line
with its token counts and a cost calculated from config/prices.json (a calculation, not a bill).
Eval providers return their lines in each promptfoo result (response.metadata.audit); live runs
also leave runs/<id>/audit.jsonl. All lines are collected, identical lines are counted once (a
workflow run appears in both places), and tokens and cost are summed. Not included: LLM-judge calls
made by the writer suite's `tone` and `no-new-findings` criteria, which write no audit line.

Usage:
    .venv/bin/python scripts/build_evidence.py [--skip-pytest]
"""
import argparse
import csv
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULTS_DIRNAME, RUNS_DIRNAME = "results", "runs"
WORKFLOW_SUITE = "workflow"
CSV_COLUMNS = ["suite", "case", "criterion", "passed", "total", "pass_rate", "observed"]
ALL_ASSERTIONS, ALL_CASES = "(all assertions)", "(all cases)"
UNNAMED = "(unnamed)"


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def promptfoo_results(path):
    """The individual results (one per case per repeat) of a promptfoo `-o` file; [] if unreadable."""
    data = read_json(path)
    return ((data or {}).get("results") or {}).get("results") or []


def suite_name(path):
    return Path(path).stem


# --- success metrics -------------------------------------------------------------------------

def _rate(passed, total):
    return round(passed / total, 4) if total else 0.0


def _row(suite, case, criterion, passed, total, observed=""):
    return {"suite": suite, "case": case, "criterion": criterion, "passed": passed, "total": total,
            "pass_rate": _rate(passed, total), "observed": observed}


def _case_of(result):
    return (result.get("testCase") or {}).get("description") or "(no description)"


def _named_assertions(result):
    """(metric, passed) for every named assertion of a result."""
    components = (result.get("gradingResult") or {}).get("componentResults") or []
    return [((c.get("assertion") or {}).get("metric"), bool(c.get("pass"))) for c in components
            if (c.get("assertion") or {}).get("metric")]


def _observed_results(result):
    """For workflow results: the terminal state the run actually reached (empty for helper suites)."""
    try:
        return json.loads((result.get("response") or {}).get("output") or "null")["result"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def rollup(suite, results):
    """Rows for one suite: each case (all its assertions together), then each criterion across the suite."""
    by_case, by_metric = defaultdict(list), defaultdict(lambda: [0, 0])
    for result in results:
        by_case[_case_of(result)].append(result)
        for metric, passed in _named_assertions(result):
            by_metric[metric][0] += passed
            by_metric[metric][1] += 1
    rows = []
    for case, group in by_case.items():
        passed = sum(1 for r in group if r.get("success"))
        states = Counter(s for s in map(_observed_results, group) if s)
        observed = ", ".join(f"{state} x{n}" for state, n in sorted(states.items()))
        rows.append(_row(suite, case, ALL_ASSERTIONS, passed, len(group), observed))
    rows += [_row(suite, ALL_CASES, metric, passed, total) for metric, (passed, total) in sorted(by_metric.items())]
    return rows


def success_rows(result_files):
    rows = []
    for path in sorted(result_files):
        results = promptfoo_results(path)
        if results:
            rows += rollup(suite_name(path), results)
    return rows


def write_csv(rows, path):
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


# --- audit summary ---------------------------------------------------------------------------

def audit_lines_from_results(results):
    """(audit lines, number of results that returned none) from promptfoo results."""
    lines, without = [], 0
    for result in results:
        audit = ((result.get("response") or {}).get("metadata") or {}).get("audit")
        if audit:
            lines += audit
        else:
            without += 1
    return lines, without


def audit_lines_from_run(run_dir):
    path = Path(run_dir) / "audit.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _fingerprint(line):
    """Identity of an audit line. Not the whole line: promptfoo's JSON turns a cost of 0.0 into 0."""
    return (line.get("correlation_id"), line.get("step"), line.get("turn"), line.get("agent"), line.get("target"),
            line.get("ts"))


def _totals(lines):
    return {"audit_lines": len(lines),
            "input_tokens": sum(e["input_tokens"] for e in lines),
            "output_tokens": sum(e["output_tokens"] for e in lines),
            "cost_usd": round(sum(e["cost_usd"] for e in lines), 6)}


def _by_model(lines):
    grouped = defaultdict(list)
    for line in lines:
        grouped[line.get("model") or "(none)"].append(line)
    return {model: _totals(group) for model, group in sorted(grouped.items())}


def summarize_audit(sources):
    """sources: {name: audit lines}, in the order they are counted. A line already counted is not counted again."""
    seen, per_source, unique = set(), {}, []
    for name, lines in sources.items():
        fresh = []
        for line in lines:
            key = _fingerprint(line)
            if key not in seen:
                seen.add(key)
                fresh.append(line)
        per_source[name] = {**_totals(fresh), "duplicate_lines_skipped": len(lines) - len(fresh)}
        unique += fresh
    return {"totals": _totals(unique), "by_model": _by_model(unique),
            "by_agent": {a: _totals([e for e in unique if e["agent"] == a]) for a in sorted({e["agent"] for e in unique})},
            "sources": per_source}


def collect_audit_sources(result_files, run_dirs):
    """Audit lines per source: result files first, then runs/ directories (whatever they add on top)."""
    sources, without = {}, {}
    for path in sorted(result_files):
        lines, missing = audit_lines_from_results(promptfoo_results(path))
        sources[f"{RESULTS_DIRNAME}/{Path(path).name}"] = lines
        if missing:
            without[f"{RESULTS_DIRNAME}/{Path(path).name}"] = missing
    for run_dir in sorted(run_dirs):
        sources[f"{RUNS_DIRNAME}/{Path(run_dir).name}"] = audit_lines_from_run(run_dir)
    return sources, without


NOTES = [
    "Cost is calculated from token counts and config/prices.json (USD per 1M tokens, input and output only); it is not a measured bill.",
    "Tokens come from the audit lines the harness wrote for every sub-agent call, every tool-loop turn and every coordinator turn.",
    "Identical audit lines found both in results/*.json and in runs/ are counted once.",
    "NOT included: LLM-judge calls made by the writer suite (tone, no-new-findings); they write no audit line.",
    "Canned sub-agents in the coordinator suite have zero tokens by design; that suite's cost is the coordinator's alone.",
]


def build_audit_summary(result_files, run_dirs, now=None):
    sources, without = collect_audit_sources(result_files, run_dirs)
    summary = summarize_audit(sources)
    return {"generated_at": (now or datetime.now(timezone.utc)).isoformat(), **summary,
            "results_without_audit_lines": without, "notes": NOTES}


# --- pytest and main ----------------------------------------------------------------------------

def run_pytest(output_path, repo=REPO):
    """Run the plain tests and keep their output as evidence. Returns pytest's exit code."""
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], cwd=repo, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    Path(output_path).write_text(done.stdout + done.stderr, encoding="utf-8")
    return done.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-pytest", action="store_true", help="do not (re)run pytest or rewrite results/pytest.txt")
    args = parser.parse_args(argv)
    results = REPO / RESULTS_DIRNAME
    result_files = sorted(results.glob("*.json")) if results.is_dir() else []
    run_dirs = [p for p in (REPO / RUNS_DIRNAME).glob("*") if p.is_dir()]
    rows = success_rows(p for p in result_files if p.name != "audit-summary.json")
    write_csv(rows, results / "success_metrics.csv")
    summary = build_audit_summary([p for p in result_files if p.name != "audit-summary.json"], run_dirs)
    (results / "audit-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {results / 'success_metrics.csv'} ({len(rows)} rows)")
    print(f"wrote {results / 'audit-summary.json'}: {summary['totals']}")
    code = 0
    if not args.skip_pytest:
        code = run_pytest(results / "pytest.txt")
        print(f"wrote {results / 'pytest.txt'} (pytest exit code {code})")
    return code


if __name__ == "__main__":
    sys.exit(main())
