"""Staleness: an artifact or review is fresh only if everything it was built on is unchanged."""
from harness.binding import expected_binding
from harness.store import REVIEWER

PASS_VERDICT = "pass"


class Freshness:
    def __init__(self, store, sandbox):
        self._store = store
        self._sandbox = sandbox

    def artifact_is_fresh(self, record):
        """Every upstream hash it recorded must still be the latest upstream artifact's hash."""
        return all(
            (latest := self._store.latest(agent)) is not None and latest.sha256 == sha
            for agent, sha in record.inputs_sha256.items()
        )

    def review_is_fresh(self, review):
        """A review applies only to the target (and diff) it was bound to, while that is itself fresh."""
        target = self._store.latest(review.target)
        if target is None or not self.artifact_is_fresh(target):
            return False
        current = expected_binding(review.target, self._store, self._sandbox)
        return all(review.binding.get(key) == value for key, value in current.items())

    def first_challenge(self, target):
        """The earliest non-pass review bound to exactly what is under review now, or None.

        Bound to the same full binding (artifact hash, and diff or draft hash where they apply),
        so a new artifact starts clean, while an identical re-run that only changes nothing
        reviewable stays challenged.
        """
        current = expected_binding(target, self._store, self._sandbox)
        if current is None:
            return None
        return next((review for review in self._store.records(REVIEWER, target)
                     if review.artifact["verdict"] != PASS_VERDICT
                     and all(review.binding.get(key) == value for key, value in current.items())), None)

    def was_challenged(self, target):
        """True when a review of the current `target` (same full binding) was not a pass.

        A pass obtained by re-reviewing unchanged work after a challenge does not count. The
        revision allowance is unchanged: a replacement identical to the challenged artifact is
        still challenged, spends the allowance and so requires a human. The writer draft is
        covered too (ReportPublisher uses has_fresh_passing_review).
        """
        return self.first_challenge(target) is not None

    def has_fresh_passing_review(self, target):
        """True when the latest `target` is fresh, its latest review is fresh and passing, and it was never challenged."""
        record = self._store.latest(target)
        review = self._store.latest(REVIEWER, target)
        return (
            record is not None and review is not None
            and self.artifact_is_fresh(record)
            and self.review_is_fresh(review)
            and review.artifact["verdict"] == PASS_VERDICT
            and not self.was_challenged(target)
        )
