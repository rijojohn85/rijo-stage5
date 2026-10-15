"""The facts of one finished coordinator run, as the JSON a promptfoo provider returns.

Shared by the coordinator governance provider (canned helpers) and the workflow provider (live
helpers), so both suites judge a run from the same evidence: audit.jsonl and the saved artifacts.
"""
import json

from run_trace import REVIEWER, audit_problems, read_steps, steps_as_dicts

from harness.audit import AuditLog


def _tokens(audit):
    prompt = sum(e["input_tokens"] for e in audit)
    completion = sum(e["output_tokens"] for e in audit)
    return {"prompt": prompt, "completion": completion, "total": prompt + completion}


def summarize_run(run_dir, outcome):
    """The run's outcome, dispatch order, audit verdict and spend. Cost is calculated from tokens, not billed."""
    steps = read_steps(run_dir)
    report = outcome.report_path.read_text(encoding="utf-8")
    return {
        "run_id": run_dir.name,
        "result": outcome.result,
        "turns": outcome.turns,
        "human_required": outcome.human_required,
        "human_reasons": list(outcome.human_reasons),
        "cost_usd": outcome.cost_usd,
        "report_head": report.splitlines()[0] if report else "",
        "steps": steps_as_dicts(steps),
        "dispatches": len(steps),
        "reviews": sum(1 for s in steps if s.agent == REVIEWER),
        "audit_problems": audit_problems(run_dir),
    }


def provider_response(summary, run_dir, log_text):
    """promptfoo's response: the summary as output, token usage and calculated cost from the run's audit.jsonl."""
    audit = AuditLog(run_dir / "audit.jsonl").entries()
    return {
        "output": json.dumps(summary),
        "cost": summary["cost_usd"],
        "tokenUsage": _tokens(audit),
        "metadata": {"audit": audit, "log": log_text},
    }
