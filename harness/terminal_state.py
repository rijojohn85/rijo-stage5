"""The completion check: is the coordinator's proposed terminal state backed by fresh evidence?

Used twice: before a writer draft is allowed, and again at finalization. Section 5 table.
"""
from harness.results import ErrorCode, Failure
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
        return (self._no_human_needed() or self._all_passing("triage", "plan", "implement")
                or self._triage_decided("fix-now") or self._implement_tests_passed() or self._no_open_findings())

    def _deferred(self):
        return self._no_human_needed() or self._all_passing("triage") or self._triage_decided("defer")

    def _punch_out(self):
        if not self._run_state.human_required():
            return _refuse("HUMAN_PUNCH_OUT needs a recorded reason a human is required")
        failure = self._all_passing("escalate")
        if failure:
            return failure
        triage = self._store.latest("triage")
        review = self._store.latest(REVIEWER, "triage")
        if triage is not None and (review is None or not self._freshness.review_is_fresh(review)):
            return _refuse("the triage artifact has no fresh review")
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
