"""What the writer is shown, and what the reviewer of a writer draft is shown beyond the draft.

Both see the same evidence: the latest source artifacts, the review of each, and the earlier
challenges with a label saying whether a newer artifact has since replaced the one challenged.
The writer is never given its own previous draft (no previous_artifact): feedback on an earlier
draft arrives as plain `writer_feedback` text, separate from the evidence.
"""
from harness.agents import ARTIFACT_CHAIN
from harness.store import REVIEWER, WRITER

SOURCES = ARTIFACT_CHAIN[WRITER][:-1]
SUPERSEDED = "superseded: a newer artifact replaced the one this review challenged"
CURRENT = "current: this is the review of the latest artifact"
STANDARD_REASONS = {
    "VERIFIED": "every step was reviewed and passed, and the plan's test passed",
    "DEFERRED": "triage decided to defer and its review passed",
}


def source_artifacts(ctx):
    return {a: r.artifact for a in SOURCES if (r := ctx.store.latest(a)) is not None}


def latest_reviews(ctx):
    """The latest review of each source artifact; `status` says pass/revise/escalate, or none/stale."""
    reviews = {}
    for agent in SOURCES:
        if ctx.store.latest(agent) is None:
            continue
        review = ctx.store.latest(REVIEWER, agent)
        reviews[agent] = {
            "target": agent, "status": ctx.run_state.review_status(agent),
            "verdict": review.artifact["verdict"] if review else None,
            "findings": review.artifact["findings"] if review else [],
        }
    return reviews


def historical_challenges(ctx):
    """Every non-passing review of a source artifact, oldest first (writer-draft reviews excluded)."""
    challenges = []
    for review in ctx.store.records(REVIEWER):
        if review.target == WRITER or review.artifact["verdict"] == "pass":
            continue
        latest = ctx.store.latest(review.target)
        replaced = latest is None or review.binding.get("reviewed_sha256") != latest.sha256
        challenges.append({"target": review.target, "step": review.step, "verdict": review.artifact["verdict"],
                           "findings": review.artifact["findings"], "status": SUPERSEDED if replaced else CURRENT})
    return challenges


def _evidence(ctx):
    return {"reviews": latest_reviews(ctx), "historical_challenges": historical_challenges(ctx),
            "human_required": ctx.run_state.human_required(), "human_reasons": ctx.run_state.human_reasons()}


def _reason(ctx):
    if ctx.terminal_state == "HUMAN_PUNCH_OUT":
        return "; ".join(ctx.run_state.human_reasons()) or "a human must decide"
    return STANDARD_REASONS[ctx.terminal_state]


def writer_message(ctx, bug_report_text):
    return {
        "bug_report": bug_report_text,
        "terminal_state": ctx.terminal_state,
        "reason": _reason(ctx),
        "required_sections": list(ctx.report.required_sections()),
        "artifacts": source_artifacts(ctx),
        **_evidence(ctx),
        "writer_feedback": ctx.feedback,
    }


def draft_review_extras(ctx):
    """For a reviewer of a writer draft: the exact draft text plus the evidence it must agree with."""
    return {"draft": ctx.store.draft_text(ctx.store.latest(WRITER)), **_evidence(ctx)}
