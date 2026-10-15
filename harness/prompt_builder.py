"""Builds the single user message for a helper from its inputs. Pure: no I/O besides git diff."""
import json

from harness.agents import ARTIFACT_CHAIN
from harness.binding import DIFF_TARGET, expected_binding
from harness.store import REVIEWER

JSON_INDENT = 2


def upstream_records(ctx, agent):
    """Latest records of the artifacts `agent` builds on (those that exist), keyed by agent."""
    found = {a: ctx.store.latest(a) for a in ARTIFACT_CHAIN[agent][:-1]}
    return {a: r for a, r in found.items() if r is not None}


def previous_artifact(ctx):
    """The producer's own latest artifact, shown only on a revision (reviewer feedback present).

    Data for the model to revise; it never feeds the binding, freshness or allowance checks.
    """
    if ctx.feedback is None:
        return None
    record = ctx.store.latest(ctx.spec.name)
    return record.artifact if record else None


def build_user_message(ctx, profile, bug_report_text):
    if ctx.spec.name == REVIEWER:
        body = _review_inputs(ctx, bug_report_text)
    else:
        body = _producer_inputs(ctx, profile, bug_report_text)
    return json.dumps(body, indent=JSON_INDENT)


def _producer_inputs(ctx, profile, bug_report_text):
    body = {
        "bug_report": bug_report_text,
        "artifacts": {a: r.artifact for a, r in upstream_records(ctx, ctx.spec.name).items()},
        "reviewer_feedback": ctx.feedback,
        "previous_artifact": previous_artifact(ctx),
    }
    if profile.shows_audit_failures:
        body["earlier_errors"] = ctx.audit.failures()
    return body


def _review_inputs(ctx, bug_report_text):
    chain = ARTIFACT_CHAIN[ctx.target]
    body = {
        "bug_report": bug_report_text,
        "review_target": ctx.target,
        "artifacts": {a: r.artifact for a in chain if (r := ctx.store.latest(a)) is not None},
        "echo_these_fields_in_your_reply": expected_binding(ctx.target, ctx.store, ctx.sandbox),
    }
    if ctx.target == DIFF_TARGET:
        body["diff"] = ctx.sandbox.diff()
    return body
