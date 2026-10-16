"""Cross-cutting runtime rules: no agentic CLI in harness/, a complete audit trail, one failure-report format."""
import ast
import json
import sys
from pathlib import Path

import pytest

from conftest import FIXED_TIME
from harness import report
from harness.tools import PROJECT_ROOT
from test_coordinator import BUG, HAPPY_COORDINATOR, HAPPY_HELPERS, build, launch, reviewing

EVALS = PROJECT_ROOT / "evals"
sys.path.insert(0, str(EVALS))

import run_trace  # noqa: E402

AGENTIC_CLIS = {"claude", "codex", "devin", "opencode"}
PROCESS_CALLS = {"run", "Popen", "call", "check_call", "check_output", "system", "popen", "spawn", "exec", "execv",
                 "execvp", "create_subprocess_exec", "create_subprocess_shell"}


def _string_constants(node):
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def agentic_cli_uses(source):
    """Names of agentic CLIs that appear among the arguments of a process-launching call."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name in PROCESS_CALLS:
            words = {w for text in _string_constants(node) for w in text.replace("/", " ").split()}
            found += sorted(words & AGENTIC_CLIS)
    return found


def test_the_scanner_catches_a_cli_used_as_a_subprocess():
    assert agentic_cli_uses("import subprocess\nsubprocess.run(['claude', '-p', 'x'])") == ["claude"]
    assert agentic_cli_uses("import os\nos.system('opencode run foo')") == ["opencode"]
    assert agentic_cli_uses("subprocess.run(['git', 'status'])") == []
    assert agentic_cli_uses("AI_IDENTITIES = ('claude', 'codex')") == []   # a name in a list is not a launch


def test_harness_never_launches_an_agentic_cli():
    sources = sorted((PROJECT_ROOT / "harness").glob("*.py"))
    assert len(sources) > 30
    offenders = {p.name: agentic_cli_uses(p.read_text(encoding="utf-8")) for p in sources}
    assert {name: used for name, used in offenders.items() if used} == {}


def test_harness_does_not_import_a_cli_wrapper_or_agent_sdk():
    banned = ("claude_agent_sdk", "claude_code_sdk", "opencode", "codex")
    for path in (PROJECT_ROOT / "harness").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            assert not [m for m in modules if m.split(".")[0] in banned], path.name


# --- audit complete ------------------------------------------------------------------------

def finished_run(tmp_path):
    coordinator, _, audit, run_dir = build(tmp_path, HAPPY_COORDINATOR, HAPPY_HELPERS)
    outcome = coordinator.run(BUG)
    assert outcome.result == "VERIFIED"
    return run_dir, outcome.turns


def drop_coordinator_lines(run_dir, keep):
    """Rewrite audit.jsonl keeping only the first `keep` coordinator turn lines."""
    path = run_dir / "audit.jsonl"
    lines, seen = [], 0
    for line in path.read_text().splitlines():
        if json.loads(line).get("agent") == "coordinator":
            seen += 1
            if seen > keep:
                continue
        lines.append(line)
    path.write_text("".join(line + "\n" for line in lines))


def test_a_finished_run_has_one_correlation_id_complete_lines_and_the_file_on_disk(tmp_path):
    run_dir, turns = finished_run(tmp_path)
    assert (run_dir / "audit.jsonl").is_file() and run_trace.audit_problems(run_dir, turns) == []
    lines = [json.loads(line) for line in (run_dir / "audit.jsonl").read_text().splitlines()]
    assert len(lines) == 16 and {line["correlation_id"] for line in lines} == {run_dir.name}
    assert all(key in line for line in lines for key in run_trace.REQUIRED_AUDIT_KEYS)
    assert all(line["ts"].startswith(FIXED_TIME.isoformat()[:16]) for line in lines)


def test_audit_check_reports_a_missing_artifact_a_second_correlation_id_and_an_incomplete_line(tmp_path):
    run_dir, turns = finished_run(tmp_path)
    (run_dir / "03-plan.json").unlink()
    path = run_dir / "audit.jsonl"
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    lines[0]["correlation_id"] = "other"
    del lines[1]["cost_usd"]
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    problems = " | ".join(run_trace.audit_problems(run_dir, turns))
    assert "03-plan.json is not on disk" in problems and "'other'" in problems and "lacks ['cost_usd']" in problems


def test_audit_check_fails_when_there_is_no_audit_file(tmp_path):
    assert run_trace.audit_problems(tmp_path, 1)


def test_audit_check_reports_deleted_coordinator_lines(tmp_path):
    run_dir, turns = finished_run(tmp_path)
    drop_coordinator_lines(run_dir, keep=0)
    assert any("0 coordinator turn line(s), the run made" in p for p in run_trace.audit_problems(run_dir, turns))


def test_audit_check_reports_the_last_coordinator_lines_dropped(tmp_path):
    run_dir, turns = finished_run(tmp_path)
    drop_coordinator_lines(run_dir, keep=turns - 2)
    assert any(f"{turns - 2} coordinator turn line(s)" in p for p in run_trace.audit_problems(run_dir, turns))


# --- one failure format ------------------------------------------------------------------------

@pytest.mark.parametrize("result", ["FAILED_COORDINATION", "FAILED_TURN_BUDGET", "FAILED_COORDINATOR_API"])
def test_every_failure_reason_uses_the_same_report_format_and_keeps_the_human_requirement(result):
    labels = {a: report.Label(report.NOT_RUN) for a in ("screen", "triage", "plan", "implement", "escalate")}
    text = report.render_failure(result, True, ["triage step 1 flagged escalation"], labels,
                                 [{"step": 3, "agent": "writer", "error_code": "PRECONDITION_MISSING", "detail": "x"}], [])
    assert text.startswith(f"# Result: {result}\n\n**Human decision required**")
    assert report.result_of(text) == result
    assert [line for line in report.all_heading_lines(text) if line.startswith("## ")] == [
        "## Screen: NOT RUN", "## Triage: NOT RUN", "## Plan: NOT RUN", "## Implement: NOT RUN", "## Escalation: NOT RUN", "## What failed",
        "## Why a human is required", "## Writer drafts (not accepted)"]
    assert "triage step 1 flagged escalation" in text and "step 3 writer: PRECONDITION_MISSING" in text
