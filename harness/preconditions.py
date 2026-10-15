"""Pre-model checks. Each is a small object with run(ctx) -> Failure | None.

They run before any API call, so a missing or stale input costs nothing.
"""
from harness.results import ErrorCode, Failure

MAX_ARTIFACTS_PER_PRODUCER = 2  # the initial artifact plus one replacement per run


def _missing(detail):
    return Failure(ErrorCode.PRECONDITION_MISSING, detail)


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
