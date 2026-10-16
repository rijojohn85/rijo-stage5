"""Runs one eval case through the real Dispatcher (harness/dispatcher.py) with an injected LLM.

Upstream artifacts from the fixtures are saved into a fresh run directory as if earlier steps
had succeeded and passed review, so the dispatcher's own preconditions, tool loop, checks
and audit run exactly as they do in a real run. Nothing here re-implements a rule.
"""
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from clean_screen import PASSING_REVIEW, seed_clean_screen
from eval_case import WRITER_TARGET
from fixed_diff_sandbox import FixedDiffSandbox
from history import seed_history
from scratch_workspace import scratch_workspace

from harness.agents import ARTIFACT_CHAIN
from harness.audit import AuditLog
from harness.binding import expected_binding
from harness.sandbox_git import GitSandbox
from harness.store import REVIEWER, WRITER, ArtifactStore
from harness.tools import run_test
from harness.wiring import build_dispatcher

IMPLEMENT = "implement"
TRIAGE = "triage"


@dataclass(frozen=True)
class CaseOutcome:
    result: dict                     # exactly what the coordinator would receive
    audit: list = field(default_factory=list)
    observed: dict = field(default_factory=dict)  # facts measured outside the model (implement only)


def seed_upstream(store, sandbox, case):
    """Save the case's fixtures as earlier steps; producers also get a passing review of each."""
    steps = iter(range(1, 100))
    reviewed = case.agent != REVIEWER or case.target == WRITER_TARGET  # a writer draft is reviewed against reviewed sources
    for name in ARTIFACT_CHAIN.get(case.target or case.agent, ()):
        if name not in case.artifacts:
            continue
        inputs = {up: store.latest(up).sha256 for up in ARTIFACT_CHAIN[name][:-1] if store.latest(up)}
        store.save(next(steps), name, None, case.artifacts[name], inputs, {})
        if reviewed:
            binding = expected_binding(name, store, sandbox)
            store.save(next(steps), REVIEWER, name, PASSING_REVIEW, {}, binding)


def seed_previous(store, case):
    """A revision case: the producer's own first-pass artifact is already in the store."""
    if case.previous is not None and case.agent != REVIEWER:
        inputs = {up: store.latest(up).sha256 for up in ARTIFACT_CHAIN[case.agent][:-1] if store.latest(up)}
        store.save(len(store.records()) + 1, case.agent, None, case.previous, inputs, {})


def seed_draft(store, case):
    """A writer-review case: the fixture draft is saved as the latest writer attempt, built on the sources."""
    if case.target != WRITER_TARGET:
        return
    inputs = {a: store.latest(a).sha256 for a in ARTIFACT_CHAIN[WRITER][:-1] if store.latest(a)}
    record = store.save(len(store.records()) + 1, WRITER, None, {"sections": {"overview": case.draft}}, inputs, {})
    store.write_draft(record, case.draft)


def _observe_draft(store, result):
    """The rendered draft the writer produced: code assembled it, so criteria judge it, not just the JSON."""
    return {"draft": store.draft_text(store.latest(WRITER))} if result["ok"] else {}


PYTEST_TESTS_FAILED = 1  # pytest exit codes: 0 passed, 1 tests failed, 2 collection error, 5 no tests


def _plan_test(case):
    """The plan's test files: the new pytest files the implementer creates."""
    return case.artifacts["plan"].get("test_files", [])


def _failed_on_original_code(workspace, test_files):
    """Red check: replay the implementer's test files on a fresh, unedited copy of the sandbox.

    Red means the tests ran and failed (exit 1). A test file the implementer never created, a
    collection error or a test that passes on the original code is not red. Evals only; the
    live harness trusts the implementer's `test_passed`.
    """
    with scratch_workspace() as original:
        for relative in test_files:
            created = workspace / relative
            if created.is_file():
                (original / relative).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(created, original / relative)
        replay = run_test(test_files, root=original)
    return bool(replay.get("ok")) and replay["exit_code"] == PYTEST_TESTS_FAILED


def _observe_implement(workspace, sandbox, case, result):
    """What the model cannot be trusted to report: git's view of the edits and the test's real runs."""
    test_files = _plan_test(case)
    after = run_test(test_files, root=workspace) if result["ok"] else None
    return {
        "actual_changed": sandbox.changed_files(),
        "test_failed_before": result["ok"] and _failed_on_original_code(workspace, test_files),
        "test_passed_after": bool(after and after.get("passed")),
    }


def run_case(case, llm):
    with scratch_workspace() as workspace, tempfile.TemporaryDirectory(prefix="eval-run-") as directory:
        run_dir = Path(directory) / "run"
        bug_report_path = Path(directory) / "bug.md"
        bug_report_path.write_text(case.bug_report, encoding="utf-8")
        if case.agent == REVIEWER:
            sandbox = FixedDiffSandbox(case.artifacts.get(IMPLEMENT, {}).get("changed", ()), case.diff)
        else:
            sandbox = GitSandbox(workspace)
        store = ArtifactStore(run_dir)
        if case.agent == TRIAGE:
            seed_clean_screen(store, sandbox)
        seed_upstream(store, sandbox, case)
        seed_history(store, sandbox, case.history)
        seed_previous(store, case)
        seed_draft(store, case)
        dispatcher = build_dispatcher(
            llm=llm, run_dir=run_dir, tool_root=workspace, sandbox=sandbox, bug_report_path=bug_report_path,
            correlation_id="eval", clock=lambda: datetime.now(timezone.utc),
        )
        result = dispatcher.dispatch(case.agent, target=case.target, feedback=case.feedback,
                                     terminal_state=case.terminal_state)
        observed = _observe_implement(workspace, sandbox, case, result) if case.agent == IMPLEMENT else {}
        observed = _observe_draft(store, result) if case.agent == WRITER else observed
        return CaseOutcome(result, AuditLog(run_dir / "audit.jsonl").entries(), observed)
