#!/usr/bin/env python3
"""promptfoo provider: one bug report through the real harness, end to end.

This calls harness.run.run_bug (the coordinator on glm-5p3, every sub-agent on deepseek, the real
dispatcher, checks and publisher) with the Fireworks client: no CLI, no stand-in. A case is one
run; `--repeat 3` makes three. The run writes runs/<id>/ as any run does, and its audit.jsonl is
what the criteria read. It never records a human decision: disposition.json is only computed.

Runs edit the real sandbox/, so each one starts from and ends with `git checkout HEAD -- sandbox`
(plus a clean of untracked files), or runs would contaminate each other.
Needs FIREWORKS_API_KEY (load .env first) and makes paid API calls.
"""
import io
import subprocess
import sys
import tempfile
from pathlib import Path

PROVIDERS_DIR = Path(__file__).resolve().parent
EVALS_DIR = PROVIDERS_DIR.parent
REPO = EVALS_DIR.parent
for entry in (str(REPO), str(EVALS_DIR), str(PROVIDERS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from run_summary import provider_response, summarize_run  # noqa: E402

from harness.llm import FireworksClient  # noqa: E402
from harness.run import RUNS_DIRNAME, run_bug, utc_now  # noqa: E402
from harness.sandbox_git import GitSandbox  # noqa: E402
from harness.tools import SANDBOX_DIRNAME  # noqa: E402


def reset_sandbox(root):
    """Every run starts from the same buggy sandbox, or a later run would find the fix already applied."""
    for args in (["checkout", "HEAD", "--", SANDBOX_DIRNAME], ["clean", "-fdq", SANDBOX_DIRNAME]):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, stdin=subprocess.DEVNULL)


def run_workflow(report, llm, root, runs_dir):
    """The run's response; the sandbox is reset before and after, whatever happens in between."""
    reset_sandbox(root)
    try:
        with tempfile.TemporaryDirectory(prefix="workflow-eval-") as directory:
            bug = Path(directory) / "bug.md"
            bug.write_text(report, encoding="utf-8")
            log = io.StringIO()
            run_dir, outcome = run_bug(bug, llm=llm, runs_dir=runs_dir, tool_root=root, sandbox=GitSandbox(root),
                                       clock=utc_now, stderr=log)
            return provider_response(summarize_run(run_dir, outcome), run_dir, log.getvalue())
    finally:
        reset_sandbox(root)


def call_api(prompt, options, context, llm=None, root=REPO, runs_dir=None):
    """promptfoo entry point. `llm`, `root` and `runs_dir` are for tests; promptfoo passes none of them."""
    try:
        report = ((context or {}).get("vars") or {})["report"]
        return run_workflow(report, llm or FireworksClient.from_env(), root, runs_dir or root / RUNS_DIRNAME)
    except (KeyError, subprocess.CalledProcessError, OSError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


if __name__ == "__main__":
    sys.stderr.write("internal provider; call via promptfoo\n")
    sys.exit(2)
