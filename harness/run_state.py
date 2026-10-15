"""What is true of the run so far, computed from saved artifacts and the audit log.

Staleness comes from Freshness; `human_required` scans everything saved, stale or not, so a
later passing artifact can never erase an earlier reason a person must decide.
"""
from harness.preconditions import MAX_ARTIFACTS_PER_PRODUCER
from harness.results import ErrorCode
from harness.store import REVIEWER

PRODUCERS = ("triage", "plan", "implement", "escalate")
UNRECOVERED_FAILURES = 2  # an errored call is retried once; a second consecutive failure is unrecoverable
NO_REVIEW, STALE_REVIEW = "none", "stale"


def _review_requires_human(review):
    artifact = review.artifact
    return artifact["verdict"] == "escalate" or any(f["category"] == "dangerous" for f in artifact["findings"])


class RunState:
    def __init__(self, store, audit, freshness):
        self._store, self._audit, self._freshness = store, audit, freshness

    # --- staleness and the per-producer revision allowance -------------------------------------

    def is_fresh(self, agent):
        record = self._store.latest(agent)
        return record is not None and self._freshness.artifact_is_fresh(record)

    def review_status(self, agent):
        """pass / revise / escalate for the review of the latest artifact; none or stale otherwise."""
        review = self._store.latest(REVIEWER, agent)
        if review is None:
            return NO_REVIEW
        return review.artifact["verdict"] if self._freshness.review_is_fresh(review) else STALE_REVIEW

    def replacements_left(self, agent):
        return max(0, MAX_ARTIFACTS_PER_PRODUCER - len(self._store.records(agent)))

    def allowance_spent_on_a_challenge(self, agent):
        """The latest artifact was challenged by its review but no replacement is allowed."""
        latest, review = self._store.latest(agent), self._store.latest(REVIEWER, agent)
        return (latest is not None and review is not None and self.replacements_left(agent) == 0
                and review.binding.get("reviewed_sha256") == latest.sha256
                and review.artifact["verdict"] != "pass")

    # --- human_required ------------------------------------------------------------------------

    def human_reasons(self):
        return (self._triage_escalations() + self._review_escalations()
                + self._exhausted_allowances() + self._unrecovered_errors())

    def human_required(self):
        return bool(self.human_reasons())

    def _triage_escalations(self):
        return [f"triage step {r.step} flagged escalation" for r in self._store.records("triage")
                if r.artifact["escalation"]]

    def _review_escalations(self):
        return [f"review step {r.step} of {r.target} found an escalate/dangerous problem"
                for r in self._store.records(REVIEWER) if _review_requires_human(r)]

    def _exhausted_allowances(self):
        capped = {e["agent"] for e in self._audit.dispatches() if e["error_code"] == str(ErrorCode.REVISION_CAP_REACHED)}
        capped |= {a for a in PRODUCERS if self.allowance_spent_on_a_challenge(a)}
        return [f"{agent} revision allowance exhausted" for agent in sorted(capped)]

    def _unrecovered_errors(self):
        streaks, unrecovered = {}, []
        for entry in self._audit.dispatches():
            key = (entry["agent"], entry["target"])
            streaks[key] = 0 if entry["ok"] else streaks.get(key, 0) + 1
            if streaks[key] == UNRECOVERED_FAILURES and not self._is_caller_mistake(entry):
                unrecovered.append(f"{entry['agent']} failed {UNRECOVERED_FAILURES} times in a row ({entry['error_code']})")
        return unrecovered

    @staticmethod
    def _is_caller_mistake(entry):
        return entry["error_code"] in (str(ErrorCode.PRECONDITION_MISSING), str(ErrorCode.REVISION_CAP_REACHED))

    # --- what the coordinator is shown --------------------------------------------------------

    def snapshot(self):
        return {
            "human_required": self.human_required(),
            "human_reasons": self.human_reasons(),
            "artifacts": {
                agent: {"exists": self._store.latest(agent) is not None, "fresh": self.is_fresh(agent),
                        "review": self.review_status(agent), "replacements_left": self.replacements_left(agent)}
                for agent in PRODUCERS
            },
        }
