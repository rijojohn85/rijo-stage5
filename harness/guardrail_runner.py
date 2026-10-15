"""Calls the guardrails/ scripts in-process through their own main(argv), unchanged.

They print and return an exit code; this wrapper captures both so the dispatcher reuses
their logic instead of copying it.
"""
import contextlib
import io
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GuardrailResult:
    exit_code: int
    stdout: str
    stderr: str


def run_guardrail(main, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        exit_code = main(argv)
    return GuardrailResult(exit_code, out.getvalue(), err.getvalue())


@contextlib.contextmanager
def temp_json_file(value, name="candidate.json"):
    """The guardrails read files; give them the candidate output without touching the run dir."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / name
        path.write_text(json.dumps(value), encoding="utf-8")
        yield path
