#!/usr/bin/env python3
"""Workflow-level eval provider.

Runs the whole chain in one promptfoo case: triage -> plan -> implement -> review, with the
same branching the skills use (defer at triage, punch out on escalation, verify on a pass).

This is an EVAL, not the runtime. Interactive use is the five skills, driven by a human, and
nothing here runs on that path. It exists for two reasons the certification asks for:

  * the workflow is itself a Stage 3 prompt and must be evaluated as one;
  * it can assert the audit trail end to end, which no single-agent eval can.

Every case resets `sandbox/` first, so the cases cannot interfere with each other.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from opencode_cli import REPO, call_opencode, get_usage, log_usage  # noqa: E402

PROMPTS = REPO / "prompts"
DEFAULT_MODEL = "opencode-go/deepseek-v4.1-flash"


def render(name, **variables):
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text


def shell(args, cwd):
    return subprocess.run(
        args, cwd=str(cwd), capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )


def reset_sandbox(cwd):
    """Each case must start from the same buggy state, or a later case finds the fix done."""
    shell(["git", "checkout", "--", "sandbox/"], cwd)
    shell(["git", "clean", "-fdq", "sandbox/"], cwd)


class Chain:
    def __init__(self, model, cwd, step_timeout, label):
        self.model = model
        self.cwd = cwd
        self.step_timeout = step_timeout
        self.label = label
        self.steps = []
        self.cost = 0.0

    def step(self, name, prompt):
        label = f"{self.label}:{name}"
        output, session_id, elapsed = call_opencode(
            prompt, self.model, self.cwd, self.step_timeout, label=label,
        )
        tokens, cost = get_usage(session_id)
        log_usage(self.model, session_id, tokens, cost, elapsed, label)
        if cost:
            self.cost += cost

        record = {
            "step": name,
            "ok": True,
            "session_id": session_id,
            "elapsed_s": elapsed,
            "cost_usd": cost,
            "error": None,
        }
        try:
            record["output"] = json.loads(output)
        except json.JSONDecodeError as exc:
            record["ok"] = False
            record["error"] = f"output is not JSON: {exc}"
            # Keep enough of the raw reply to diagnose it. A 400-char cap once hid the
            # actual defect, which sat past the cut.
            record["raw"] = output[:4000]
        self.steps.append(record)
        return record


def finish(chain, status, route, verify=None):
    return {
        "status": status,
        "route": route,
        # `raw` is only ever set on a step that failed, so it survives here on purpose:
        # the first diagnosis of a bad step should not require guesswork.
        "steps": chain.steps,
        "cost_usd": round(chain.cost, 6),
        "verify": verify or {},
    }


def run_workflow(report, model, cwd, step_timeout, label):
    reset_sandbox(cwd)
    chain = Chain(model, cwd, step_timeout, label)

    triage_step = chain.step("triage", render("triage", report=report))
    if not triage_step["ok"]:
        return finish(chain, "FAILED_triage", None)
    triage = triage_step["output"]

    if triage.get("escalation") is True:
        return finish(chain, "HUMAN_PUNCH_OUT", "ESCALATE")
    if triage.get("decision") != "fix-now":
        return finish(chain, "DEFERRED", "DEFER")

    plan_step = chain.step("plan", render("plan", triage=json.dumps(triage, indent=2)))
    if not plan_step["ok"]:
        return finish(chain, "FAILED_plan", None)
    plan = plan_step["output"]

    implement_step = chain.step(
        "implement", render("implement", plan=json.dumps(plan, indent=2)),
    )
    if not implement_step["ok"]:
        return finish(chain, "FAILED_implement", None)
    implement = implement_step["output"]

    diff = shell(["git", "diff", "--", "sandbox/"], cwd).stdout
    review_step = chain.step("review", render(
        "review",
        triage=json.dumps(triage, indent=2),
        plan=json.dumps(plan, indent=2),
        implement=json.dumps(implement, indent=2),
        diff=diff,
    ))
    if not review_step["ok"]:
        return finish(chain, "FAILED_review", None)
    review = review_step["output"]

    verify = {
        "changed": implement.get("changed"),
        "test_passed": implement.get("test_passed"),
        "verdict": review.get("verdict"),
        "findings": review.get("findings"),
    }

    if review.get("verdict") == "pass":
        return finish(chain, "VERIFIED", "FIX", verify)
    if review.get("verdict") == "escalate":
        return finish(chain, "HUMAN_PUNCH_OUT", "ESCALATE", verify)
    # revise: the loop is a human-driven rule in SKILL.md and is capped at one revision.
    # /implement takes a plan, not findings, so this eval records the verdict rather than
    # looping and pretending the second pass is a real revision.
    return finish(chain, "NEEDS_REVISION", "REVISE", verify)


def call_api(prompt, options, context):  # noqa: ARG001 - promptfoo protocol
    cfg = (options or {}).get("config") or {}
    model = cfg.get("model") or DEFAULT_MODEL
    step_timeout = (cfg.get("step_timeout") or 300_000) / 1000
    workspace = cfg.get("workspace")
    cwd = REPO / workspace if workspace else REPO
    label = str(((context or {}).get("test") or {}).get("description") or "workflow")[:40]

    result = run_workflow(prompt, model, cwd, step_timeout, label)
    return {"output": json.dumps(result), "cost": result.get("cost_usd")}


if __name__ == "__main__":
    sys.stderr.write("internal provider; call via promptfoo\n")
    sys.exit(2)
