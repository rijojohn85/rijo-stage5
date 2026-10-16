"""The completion check: is the coordinator's proposed terminal state backed by fresh evidence?

Used twice: before a writer draft is allowed, and again at finalization. Section 5 table.
Every state first applies one shared review rule to all producer artifacts; replaced (stale)
attempts are skipped, so only the latest attempt of each artifact needs a fresh bound review.
"""
from harness.results import ErrorCode, Failure
from harness.run_state import PRODUCERS
from harness.store import REVIEWER

VERIFIED, DEFERRED, HUMAN_PUNCH_OUT = "VERIFIED", "DEFERRED", "HUMAN_PUNCH_OUT"


def _refuse(detail):
    return Failure(ErrorCode.PRECONDITION_MISSING, detail)


class TerminalStateCheck:
    def __init__(self, store, freshness, run_state):
        self._store, self._freshness, self._run_state = store, freshness, run_state
        self._rules = {VERIFIED: self._verified, DEFERRED: self._deferred, HUMAN_PUNCH_OUT: self._punch_out}

    def run(self, proposed):
        """None when `proposed` is legal now, otherwise the Failure saying what evidence is missing."""
        rule = self._rules.get(proposed)
        if rule is None:
            return _refuse(f"unknown terminal state {proposed!r}")
        return rule()

    def _verified(self):
        return (self._every_artifact_reviewed(require_pass=True) or self._no_human_needed() or self._all_passing("triage", "plan", "implement")
                or self._triage_decided("fix-now") or self._implement_tests_passed() or self._no_open_findings())

    def _deferred(self):
        return (self._every_artifact_reviewed(require_pass=True) or self._no_human_needed()
                or self._all_passing("triage") or self._triage_decided("defer"))

    def _punch_out(self):
        failure = self._every_artifact_reviewed(require_pass=False)
        if failure:
            return failure
        if not self._run_state.human_required():
            return _refuse("HUMAN_PUNCH_OUT needs a recorded reason a human is required")
        return self._all_passing("escalate")

    def _every_artifact_reviewed(self, require_pass):
        """Each producer's latest fresh artifact needs a fresh bound review (a passing one if `require_pass`).

        Replaced (stale) attempts are skipped: only the latest attempt of each artifact is reviewed.
        """
        for agent in PRODUCERS:
            record = self._store.latest(agent)
            if record is None or not self._freshness.artifact_is_fresh(record):
                continue
            if require_pass:
                failure = self._all_passing(agent)
            else:
                review = self._store.latest(REVIEWER, agent)
                failure = None if review and self._freshness.review_is_fresh(review) else _refuse(
                    f"the {agent} artifact has no fresh review")
            if failure:
                return failure
        return None

    def _no_human_needed(self):
        if self._run_state.human_required():
            return _refuse("a human is required: " + "; ".join(self._run_state.human_reasons()))
        return None

    def _all_passing(self, *agents):
        for agent in agents:
            if not self._freshness.has_fresh_passing_review(agent):
                return _refuse(f"needs a fresh {agent} with a fresh passing review")
        return None

    def _triage_decided(self, decision):
        if self._store.latest("triage").artifact["decision"] != decision:
            return _refuse(f"triage did not decide {decision}")
        return None

    def _implement_tests_passed(self):
        if not self._store.latest("implement").artifact["test_passed"]:
            return _refuse("the implementation's test did not pass")
        return None

    def _no_open_findings(self):
        for agent in ("triage", "plan", "implement"):
            if self._store.latest(REVIEWER, agent).artifact["findings"]:
                return _refuse(f"the {agent} review has unresolved findings")
        return None
