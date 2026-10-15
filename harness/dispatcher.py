"""Runs one sub-agent: preconditions -> fresh API conversation -> parse -> checks -> save -> audit.

The dispatcher only orchestrates. Every rule lives in a small object it is handed:
LLM client, tool loop, preconditions/checks (agents.py), store, audit log, rejected writer.
The coordinator receives {ok: true, ...} or a structured error, never invalid output.
"""
import json
from dataclasses import dataclass, replace

from harness.binding import expected_binding
from harness.context import DispatchContext
from harness.llm import Usage
from harness.prompt_builder import build_user_message, upstream_records
from harness.results import ErrorCode, Failure, error_result, ok_result
from harness.store import REVIEWER, WRITER
from harness.tool_schemas import tool_definitions


@dataclass(frozen=True)
class Attempt:
    """What happened before the result is built and audited."""
    failure: Failure | None = None
    record: object = None
    summary: str = ""
    usage: Usage = Usage()
    refused_tool_calls: int = 0


def first_failure(rules, *args):
    """The first rule that objects, so rules stay an ordered list behind one interface."""
    for rule in rules:
        failure = rule.run(*args)
        if failure:
            return failure
    return None


def parse_json_object(text):
    """Strict: the whole reply must be one JSON object (no prose, no fences)."""
    try:
        value = json.loads(text or "")
    except json.JSONDecodeError as exc:
        return None, Failure(ErrorCode.NOT_JSON, f"reply is not valid JSON: {exc}", text)
    if not isinstance(value, dict):
        return None, Failure(ErrorCode.NOT_JSON, "reply is JSON but not an object", text)
    return value, None


class Dispatcher:
    def __init__(self, *, specs, profiles, tool_loop, prompts, prices, views, sandbox,
                 rejected, bug_report_path, correlation_id, clock):
        self._specs, self._profiles = specs, profiles
        self._tool_loop, self._prompts, self._prices = tool_loop, prompts, prices
        self._views, self._sandbox = views, sandbox
        self._store, self._freshness, self._audit = views.store, views.freshness, views.audit
        self._rejected = rejected
        self._bug_report_path = bug_report_path
        self._correlation_id, self._clock = correlation_id, clock

    def dispatch(self, agent, target=None, feedback=None, terminal_state=None):
        target = target if agent == REVIEWER else None  # only reviews are aimed at a target
        terminal_state = terminal_state if agent == WRITER else None  # only the writer proposes a state
        step = self._audit.next_step()
        attempt = self._attempt(step, agent, target, feedback, terminal_state)
        self._record_outcome(step, agent, target, attempt)
        if attempt.failure:
            return error_result(agent, attempt.failure)
        return ok_result(agent, attempt.summary, attempt.record.artifact)

    def _attempt(self, step, agent, target, feedback, terminal_state):
        spec, profile = self._specs.get(agent), self._profiles.get(agent)
        if spec is None or profile is None:
            return Attempt(Failure(ErrorCode.PRECONDITION_MISSING, f"agent {agent!r} cannot be dispatched yet"))
        ctx = DispatchContext(spec, target, feedback, self._store, self._freshness, self._sandbox,
                              self._audit, self._bug_report_path, terminal_state, self._views.state,
                              self._views.terminal_check, self._views.report)
        failure = first_failure(profile.preconditions, ctx)
        if failure:
            return Attempt(failure)
        try:
            bug_report = self._bug_report_path.read_text(encoding="utf-8")
        except OSError as exc:
            return Attempt(Failure(ErrorCode.PRECONDITION_MISSING, f"bug report unreadable: {exc}"))
        return self._converse(step, ctx, profile, bug_report)

    def _converse(self, step, ctx, profile, bug_report):
        outcome = self._tool_loop.run(
            ctx.spec.model,
            self._prompts.system_prompt(ctx.spec.workflow),
            build_user_message(ctx, profile, bug_report),
            tool_definitions(ctx.spec.tools),
        )
        measured = {"usage": outcome.usage, "refused_tool_calls": outcome.refused_tool_calls}
        if outcome.failure:
            return Attempt(outcome.failure, **measured)
        artifact, failure = parse_json_object(outcome.content)
        failure = failure or first_failure(profile.checks, ctx, artifact)
        if failure:
            return Attempt(replace(failure, raw_output=outcome.content), **measured)
        record = self._save(step, ctx, artifact)
        return Attempt(record=record, summary=profile.summarize(record.artifact), **measured)

    def _save(self, step, ctx, artifact):
        name = ctx.spec.name
        if name == REVIEWER:
            binding = expected_binding(ctx.target, self._store, self._sandbox)
            return self._store.save(step, name, ctx.target, artifact, {}, binding)
        inputs = {a: r.sha256 for a, r in upstream_records(ctx, name).items()}
        record = self._store.save(step, name, None, artifact, inputs, {})
        if name == WRITER:  # the draft is immutable evidence next to its artifact; the reviewer is bound to its hash
            self._store.write_draft(record, ctx.report.draft(ctx.terminal_state, artifact["sections"]))
        return record

    def _record_outcome(self, step, agent, target, attempt):
        if attempt.failure:
            self._rejected.write(step, agent, target, attempt.failure)
        self._audit.append(self._audit_entry(step, agent, target, attempt))

    def _audit_entry(self, step, agent, target, attempt):
        spec = self._specs.get(agent)
        model = spec.model if spec else None
        failure, record = attempt.failure, attempt.record
        return {
            "correlation_id": self._correlation_id, "step": step, "agent": agent, "target": target,
            "model": model,
            "input_tokens": attempt.usage.input_tokens, "output_tokens": attempt.usage.output_tokens,
            "cost_usd": self._prices.cost_usd(model, attempt.usage) if model else 0.0,
            "ok": failure is None,
            "artifact": record.filename if record else None,
            "sha256": record.sha256 if record else None,
            "error_code": str(failure.code) if failure else None,
            "detail": failure.detail if failure else None,
            "refused_tool_calls": attempt.refused_tool_calls,
            "ts": self._clock().isoformat(),
        }
