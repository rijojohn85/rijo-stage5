"""Pre-model checks. Each is a small object with run(ctx) -> Failure | None.

They run before any API call, so a missing or stale input costs nothing.
"""
from harness.results import ErrorCode, Failure

MAX_ARTIFACTS_PER_PRODUCER = 2  # the initial artifact plus one replacement per run


def _missing(detail):
    return Failure(ErrorCode.PRECONDITION_MISSING, detail)


class ScreenIsClean:
    """Triage reads the bug report only after the screen rated it clean (its review is checked separately)."""

    def run(self, ctx):
        screen = ctx.store.latest("screen")
        if screen is None:
            return _missing("triage needs a screen of the bug report first (launch_screen)")
        verdict = screen.artifact["verdict"]
        if verdict != "clean":
            return _missing(f"the screen rated the report {verdict}; a human is required and triage cannot run")
        return None


class TriageIsFixNow:
    def run(self, ctx):
        triage = ctx.store.latest("triage")
        if triage is None:
            return _missing("plan needs a triage artifact")
        if triage.artifact["decision"] != "fix-now" or triage.artifact["escalation"]:
            return _missing("triage did not decide fix-now without escalation")
        return None


class FreshPassingReviewOf:
    def __init__(self, target):
        self._target = target

    def run(self, ctx):
        if not ctx.freshness.has_fresh_passing_review(self._target):
            return _missing(f"needs a fresh {self._target} with a fresh passing review")
        return None


class ReviewableTarget:
    """A review needs a valid, current artifact of an allowed target type."""

    def run(self, ctx):
        if ctx.target not in ctx.spec.reviews:
            return _missing(f"target must be one of {', '.join(ctx.spec.reviews)}")
        record = ctx.store.latest(ctx.target)
        if record is None:
            return _missing(f"no valid {ctx.target} artifact to review")
        if not ctx.freshness.artifact_is_fresh(record):
            return _missing(f"the {ctx.target} artifact is stale; rebuild it before review")
        return None


class WithinRevisionAllowance:
    def run(self, ctx):
        if len(ctx.store.records(ctx.spec.name)) >= MAX_ARTIFACTS_PER_PRODUCER:
            return Failure(ErrorCode.REVISION_CAP_REACHED,
                           f"{ctx.spec.name} already used its initial artifact and one replacement")
        return None


class NoHumanCheckpoint:
    """Work that moves the fix forward stops while a person must decide; list it last so other refusals keep their codes."""

    def run(self, ctx):
        if ctx.run_state.human_required():
            return _missing("a human is required, this agent cannot run: " + "; ".join(ctx.run_state.human_reasons()))
        return None


class TerminalStateIsLegal:
    """The writer drafts only for a terminal state the evidence supports (the Section 5 table)."""

    def run(self, ctx):
        return ctx.terminal_check.run(ctx.terminal_state)
