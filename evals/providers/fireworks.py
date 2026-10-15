#!/usr/bin/env python3
"""promptfoo provider: run one eval case through harness/dispatcher.py.

This is the real code path (tool loop, schema and scope checks, binding, audit), not a copy
of it. The model sees the same system prompt and input message a real run would send. The
promptfoo `prompt` is ignored on purpose: the test's `vars` describe the case (see
eval_case.py), and the dispatcher builds the prompt from them.

Output is the artifact JSON the dispatcher accepted. When the dispatcher rejects the model's
output, the output is the structured error ({"ok": false, "error_code": ...}) so the
criteria fail visibly instead of the case vanishing as a provider error.
Needs FIREWORKS_API_KEY (load .env first) and makes paid API calls.
"""
import json
import sys
from pathlib import Path

PROVIDERS_DIR = Path(__file__).resolve().parent
REPO = PROVIDERS_DIR.parents[1]
for entry in (str(REPO), str(PROVIDERS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from case_runner import run_case  # noqa: E402
from eval_case import CaseError, EvalCase  # noqa: E402

from harness.llm import FireworksClient  # noqa: E402


def _usage(audit):
    prompt = sum(e["input_tokens"] for e in audit)
    completion = sum(e["output_tokens"] for e in audit)
    return {"prompt": prompt, "completion": completion, "total": prompt + completion}


def _response(outcome):
    result = outcome.result
    output = result["artifact"] if result["ok"] else result
    return {
        "output": json.dumps(output),
        "cost": sum(e["cost_usd"] for e in outcome.audit),
        "tokenUsage": _usage(outcome.audit),
        "metadata": {"dispatch_ok": result["ok"], "audit": outcome.audit, **outcome.observed},
    }


def call_api(prompt, options, context, llm=None):
    """promptfoo entry point. `llm` is for tests; promptfoo never passes it."""
    try:
        case = EvalCase.from_vars((context or {}).get("vars") or {})
        return _response(run_case(case, llm or FireworksClient.from_env()))
    except CaseError as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


if __name__ == "__main__":
    sys.stderr.write("internal provider; call via promptfoo\n")
    sys.exit(2)
