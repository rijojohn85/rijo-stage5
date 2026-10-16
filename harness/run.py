"""Entry point: `python -m harness.run <bug.md>` runs the coordinator on one bug report.

Creates runs/<correlation-id>/ with 00-report.md (a copy of the bug report), the numbered
artifacts, audit.jsonl and run.log (follow it live with `tail -f runs/<id>/run.log`). Every run
ends with report.md: the writer draft that passed review, or the deterministic FAILED_<reason>
report when no draft could be accepted, and with disposition.json (the human-review view; see
harness/decisions.py).
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from harness.assessment_file import save_assessment
from harness.coordinator import MAX_TURNS
from harness.coordinator_wiring import build_coordinator
from harness.decision_log import DecisionLog
from harness.decisions import Paths
from harness.disposition import describe, read_disposition, refresh_disposition
from harness.llm import FireworksClient
from harness.report_handle import report_handle
from harness.run_log import RUN_LOG_FILENAME, RunLog
from harness.sandbox_git import GitSandbox
from harness.substance import load_assessment
from harness.tools import PROJECT_ROOT

RUNS_DIRNAME = "runs"
BUG_COPY_FILENAME = "00-report.md"
CORRELATION_ID_FORMAT = "%Y%m%d-%H%M%S"
FAILED_PREFIX = "FAILED_"


def utc_now():
    return datetime.now(timezone.utc)


def start_run(bug_path, runs_dir, clock=utc_now):
    """(correlation id, run dir, path of the saved copy of the bug report)."""
    correlation_id = clock().strftime(CORRELATION_ID_FORMAT)
    run_dir = Path(runs_dir) / correlation_id
    run_dir.mkdir(parents=True)
    copy = run_dir / BUG_COPY_FILENAME
    copy.write_text(Path(bug_path).read_text(encoding="utf-8"), encoding="utf-8")
    return correlation_id, run_dir, copy


def run_bug(bug_path, *, llm, runs_dir=PROJECT_ROOT / RUNS_DIRNAME, tool_root=PROJECT_ROOT,
            sandbox=None, clock=utc_now, stderr=sys.stderr, max_turns=MAX_TURNS, paths=Paths()):
    assessment = load_assessment(paths.substance_path, paths.manifest_path)  # fail before any paid call
    sandbox = sandbox or GitSandbox(tool_root)
    correlation_id, run_dir, bug_copy = start_run(bug_path, runs_dir, clock)
    save_assessment(run_dir, assessment)  # written once, before the first paid call
    log = RunLog.for_run(run_dir, clock, stderr)
    try:
        log.event("run-start", correlation_id=correlation_id, bug_report=bug_path, run_dir=run_dir)
        coordinator = build_coordinator(
            llm=llm, run_dir=run_dir, tool_root=tool_root, sandbox=sandbox,
            bug_report_path=bug_copy, correlation_id=correlation_id, clock=clock, log=log,
            max_turns=max_turns)
        outcome = coordinator.run(report_handle(bug_copy, correlation_id))
        refresh_disposition(run_dir, sandbox, DecisionLog(paths.decisions_path), assessment)  # initial disposition
        return run_dir, outcome
    finally:
        log.close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m harness.run", description=__doc__.splitlines()[0])
    parser.add_argument("bug_report", help="path to a bug report markdown file")
    args = parser.parse_args(argv)
    run_dir, outcome = run_bug(args.bug_report, llm=FireworksClient.from_env())
    print(f"{describe(read_disposition(run_dir))}\nresult: {outcome.result}\nhuman_required: {outcome.human_required}\n"
          f"turns: {outcome.turns}\ncost_usd (calculated): {outcome.cost_usd}\n"
          f"report: {outcome.report_path}\naudit: {run_dir / 'audit.jsonl'}\nlog: {run_dir / RUN_LOG_FILENAME}")
    return 1 if outcome.result.startswith(FAILED_PREFIX) else 0


if __name__ == "__main__":
    sys.exit(main())
