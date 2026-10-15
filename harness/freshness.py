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

    def has_fresh_passing_review(self, target):
        """True when the latest `target` artifact is fresh and its latest review is fresh and passing."""
        record = self._store.latest(target)
        review = self._store.latest(REVIEWER, target)
        return (
            record is not None and review is not None
            and self.artifact_is_fresh(record)
            and self.review_is_fresh(review)
            and review.artifact["verdict"] == PASS_VERDICT
        )
