"""Post-model checks. Each is a small object with run(ctx, artifact) -> Failure | None.

The dispatcher walks a list of them (see agents.py), so a new check never edits the dispatcher.
"""
import json

from guardrails import escalate_gate, plan_scope, screen_gate, validate
from harness.binding import expected_binding
from harness.guardrail_runner import run_guardrail, temp_json_file
from harness.results import ErrorCode, Failure
from harness.sandbox_git import SandboxGitError

GUARDRAIL_OK = 0
GUARDRAIL_BLOCKED = 1


class SchemaCheck:
    """Validates against schemas/<name>.json using guardrails/validate.py."""

    def __init__(self, schema_name):
        self._schema_name = schema_name

    def run(self, ctx, artifact):
        with temp_json_file(artifact) as path:
            result = run_guardrail(validate.main, [self._schema_name, str(path)])
        if result.exit_code != GUARDRAIL_OK:
            return Failure(ErrorCode.SCHEMA_INVALID, result.stderr.strip())
        return None


class _RepairingGate:
    """Runs a guardrails/ gate on the bug report and copies its override into the artifact in place.

    This kind of check repairs rather than rejects, so the coordinator only ever sees an
    artifact that already carries the override. Subclasses name the gate and how to word its failure.
    """
    gate_main = None   # the gate's main(argv)
    label = ""         # used in "<label> could not run"

    def run(self, ctx, artifact):
        with temp_json_file(artifact) as path:
            result = run_guardrail(self.gate_main,[str(ctx.bug_report_path), str(path), "--apply"])
            if result.exit_code == GUARDRAIL_BLOCKED:
                artifact.clear()
                artifact.update(json.loads(path.read_text(encoding="utf-8")))
        if result.exit_code not in (GUARDRAIL_OK, GUARDRAIL_BLOCKED):
            return Failure(ErrorCode.PRECONDITION_MISSING, f"{self.label} could not run: {result.stderr.strip()}")
        return None


class EscalationBackstop(_RepairingGate):
    """Forces escalation when the bug report matches a data-loss/security/outage trigger."""
    gate_main = staticmethod(escalate_gate.main)
    label = "escalation gate"


class ScreenBackstop(_RepairingGate):
    """Forces a suspicious screen when the bug report matches a known prompt-injection pattern."""
    gate_main = staticmethod(screen_gate.main)
    label = "screen gate"


class PlanScopeCheck:
    """Edits (claimed and per git) must stay within the plan's file list, via guardrails/plan_scope.py."""

    def run(self, ctx, artifact):
        try:
            actual = ctx.sandbox.changed_files()
        except SandboxGitError as exc:
            return Failure(ErrorCode.PLAN_SCOPE_VIOLATION, f"cannot read sandbox git status: {exc}")
        changed = sorted(set(artifact["changed"]) | set(actual))
        plan_path = ctx.store.path_of(ctx.store.latest("plan"))
        # --artifact (not positional paths) so a path like "--stdin" can never be read as a flag
        with temp_json_file({"changed": changed}) as changed_path:
            result = run_guardrail(plan_scope.main, [str(plan_path), "--artifact", str(changed_path)])
        if result.exit_code != GUARDRAIL_OK:
            return Failure(ErrorCode.PLAN_SCOPE_VIOLATION, result.stderr.strip())
        return None


class TestFilesInPlanCheck:
    """Every test file is also in the plan's `files`, so the plan-scope check covers the new test file."""
    __test__ = False  # not a pytest class, despite the name

    def run(self, ctx, artifact):
        outside = [path for path in artifact["test_files"] if path not in artifact["files"]]
        if outside:
            return Failure(ErrorCode.SCHEMA_INVALID,
                           f"test_files must also be listed in files; missing from files: {', '.join(outside)}")
        return None


class ReviewBindingCheck:
    """The reviewer must echo the requested target and hashes; missing or wrong values fail closed."""

    def run(self, ctx, artifact):
        expected = expected_binding(ctx.target, ctx.store, ctx.sandbox)
        wrong = [key for key, value in expected.items() if artifact.get(key) != value]
        if wrong:
            return Failure(ErrorCode.REVIEW_BINDING_MISMATCH,
                           f"review does not match the requested target; mismatched field(s): {', '.join(wrong)}")
        return None
