#!/usr/bin/env python3
"""promptfoo provider: the real coordinator (glm-5p3 on Fireworks) over canned sub-agents.

One test case is one whole run through harness.run.run_bug. The sub-agents never call a model
(canned_helpers.py), so a case measures only the coordinator's decisions: which producer it
re-runs, whether it reviews before relying, whether it respects the revision cap and whether it
finishes through a legal terminal state. The harness around it (dispatcher, checks, terminal-state
check, publisher, audit) is the production code. Needs FIREWORKS_API_KEY and makes paid calls for
the coordinator turns only.

vars: `script` (the canned sub-agent replies, JSON or an object) and `report` (the bug report text).
"""
import io
import json
import sys
import tempfile
from pathlib import Path

PROVIDERS_DIR = Path(__file__).resolve().parent
EVALS_DIR = PROVIDERS_DIR.parent
REPO = EVALS_DIR.parent
for entry in (str(REPO), str(EVALS_DIR), str(PROVIDERS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from canned_helpers import PLAN_FILE, CannedHelpers, ScriptError, SplitClient  # noqa: E402
from fixed_diff_sandbox import FixedDiffSandbox  # noqa: E402
from run_summary import provider_response, summarize_run  # noqa: E402

from harness.llm import FireworksClient  # noqa: E402
from harness.manifest import load_coordinator_spec  # noqa: E402
from harness.run import run_bug, utc_now  # noqa: E402

FAKE_DIFF = "diff --git a/sandbox/demo-app/calc.py b/sandbox/demo-app/calc.py\n+    return 0.0\n"


def _script(value):
    try:
        script = value if isinstance(value, dict) else json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ScriptError(f"var 'script' is not a JSON object: {exc}") from exc
    if not isinstance(script, dict):
        raise ScriptError("var 'script' is not a JSON object")
    return script


def run_case(case_vars, coordinator_llm):
    script = _script(case_vars.get("script") or {})
    llm = SplitClient(load_coordinator_spec().model, coordinator_llm, CannedHelpers(script))
    with tempfile.TemporaryDirectory(prefix="coordinator-eval-") as directory:
        bug = Path(directory) / "bug.md"
        bug.write_text(case_vars["report"], encoding="utf-8")
        log = io.StringIO()
        run_dir, outcome = run_bug(bug, llm=llm, runs_dir=Path(directory) / "runs", clock=utc_now, stderr=log,
                                   sandbox=FixedDiffSandbox([PLAN_FILE], FAKE_DIFF))
        return provider_response(summarize_run(run_dir, outcome), run_dir, log.getvalue())


def call_api(prompt, options, context, coordinator_llm=None):
    """promptfoo entry point. `coordinator_llm` is for tests; promptfoo never passes it."""
    try:
        return run_case((context or {}).get("vars") or {}, coordinator_llm or FireworksClient.from_env())
    except (ScriptError, KeyError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


if __name__ == "__main__":
    sys.stderr.write("internal provider; call via promptfoo\n")
    sys.exit(2)
