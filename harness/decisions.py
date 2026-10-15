"""Human decisions: `python -m harness.decisions substance|punch-out|status`.

Only a person at a terminal can record a decision: every signing command shows what is being
signed and waits for typed confirmation (EOF, such as a pipe or a non-interactive agent, counts
as declined). The coordinator and the sub-agents have no tool that reaches this module or the
log. A decision is appended to decisions/decisions.jsonl and nothing else changes: the run's
report.md and artifacts stay byte-identical; only the derived disposition.json is refreshed.
"""
import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from harness.decision_log import (DECISIONS, PUNCH_OUT, SUBSTANCE, DecisionLog, make_decision, punch_out_binding,
                                  substance_binding)
from harness.disposition import describe, refresh_disposition
from harness.manifest import MANIFEST_PATH
from harness.reviewer_identity import ReviewerError, validate_reviewer
from harness.run_facts import read_run_facts
from harness.sandbox_git import GitSandbox
from harness.substance import SUBSTANCE_PATH, SubstanceError, load_assessment
from harness.tools import PROJECT_ROOT

CONFIRM_ANSWER = "yes"


class DecisionError(Exception):
    """The decision cannot be recorded; nothing was appended."""


@dataclass(frozen=True)
class Paths:
    runs_dir: Path = PROJECT_ROOT / "runs"
    decisions_path: Path = PROJECT_ROOT / "decisions" / "decisions.jsonl"
    substance_path: Path = SUBSTANCE_PATH
    manifest_path: Path = MANIFEST_PATH


def utc_now():
    return datetime.now(timezone.utc)


def type_yes(prompt):
    """Interactive confirmation. Anything but a typed 'yes' (including EOF) declines."""
    try:
        return input(prompt).strip().lower() == CONFIRM_ANSWER
    except EOFError:
        return False


class DecisionDesk:
    """Records decisions. The clock, confirmation, output and sandbox are injected for tests."""

    def __init__(self, paths=Paths(), *, clock=utc_now, confirm=type_yes, out=sys.stdout, sandbox_for=GitSandbox):
        self._paths, self._clock, self._confirm, self._out = paths, clock, confirm, out
        self._log, self._sandbox_for = DecisionLog(paths.decisions_path), sandbox_for

    def _assessment(self):
        return load_assessment(self._paths.substance_path, self._paths.manifest_path)

    def _facts(self, run_id):
        run_dir = self._paths.runs_dir / run_id
        if not run_dir.is_dir():
            raise DecisionError(f"unknown run {run_id!r}: {run_dir} does not exist")
        return run_dir, read_run_facts(run_dir, self._sandbox_for())

    def _say(self, text=""):
        print(text, file=self._out)

    def _append(self, kind, decision, reviewer, note, binding):
        if decision not in DECISIONS:
            raise DecisionError(f"decision must be one of {', '.join(DECISIONS)}")
        entry = make_decision(timestamp=self._clock().isoformat(), reviewer=reviewer, kind=kind,
                              decision=decision, note=note, binding=binding)
        if not self._confirm(f"Type '{CONFIRM_ANSWER}' to record this {kind} {decision} as {reviewer}: "):
            raise DecisionError("not confirmed; nothing was recorded")
        self._log.append(entry)
        self._say(f"recorded {kind} {decision} by {reviewer}")
        return entry

    def sign_substance(self, decision, reviewer, note=""):
        reviewer = validate_reviewer(reviewer)
        assessment = self._assessment()
        self._say(f"substance assessment (elevated: {assessment.elevated}, binding {assessment.binding_sha256}):")
        for workflow, entry in assessment.workflows.items():
            self._say(f"  {workflow}: {entry['rating']} - {entry['why']}")
        return self._append(SUBSTANCE, decision, reviewer, note, substance_binding(assessment))

    def sign_punch_out(self, run_id, decision, reviewer, note=""):
        reviewer = validate_reviewer(reviewer)
        run_dir, facts = self._facts(run_id)
        if not facts.stopped:
            raise DecisionError(f"run {run_id} has not stopped (no report.md); a decision is accepted only after it stops")
        if not facts.human_required:
            raise DecisionError(f"run {run_id} does not require a human decision")
        self._say(facts.report_text)
        self._say(f"execution result: {facts.execution_result}")
        self._say("human required because:")
        for reason in facts.human_reasons:
            self._say(f"  - {reason}")
        entry = self._append(PUNCH_OUT, decision, reviewer, note, punch_out_binding(run_id, facts.report_sha256))
        self.status(run_id)
        return entry

    def status(self, run_id):
        run_dir, _ = self._facts(run_id)
        disposition = refresh_disposition(run_dir, self._sandbox_for(), self._log, self._assessment())
        self._say(describe(disposition))
        self._say(f"required: {', '.join(disposition['required']) or 'nothing'}")
        return disposition


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m harness.decisions", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("substance", "punch-out"):
        signing = commands.add_parser(name)
        signing.add_argument("--decision", required=True, choices=DECISIONS)
        signing.add_argument("--reviewer", default=None, help="your name (default: $USER)")
        signing.add_argument("--note", default="")
        if name == "punch-out":
            signing.add_argument("--run", required=True)
    commands.add_parser("status").add_argument("--run", required=True)
    return parser


def main(argv=None, *, desk=None, env=os.environ, err=sys.stderr):
    args = build_parser().parse_args(argv)
    desk = desk or DecisionDesk()
    try:
        if args.command == "status":
            desk.status(args.run)
            return 0
        reviewer = args.reviewer if args.reviewer is not None else env.get("USER", "")
        if args.command == "substance":
            desk.sign_substance(args.decision, reviewer, args.note)
        else:
            desk.sign_punch_out(args.run, args.decision, reviewer, args.note)
    except (DecisionError, ReviewerError, SubstanceError) as exc:
        print(f"error: {exc}", file=err)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
