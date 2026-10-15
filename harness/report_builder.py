"""Turns saved artifacts and the audit log into report facts, then into report text (via report.py).

One object answers every question the writer step and the final report ask of the run: which
sections are required, what label each step carries, and what the assembled draft or the
failure report says. It reads; it never writes.
"""
from harness import report
from harness.run_state import is_caller_mistake
from harness.store import WRITER, draft_filename

STEPS = ("triage", "plan", "implement", "escalate")
ALWAYS_REQUIRED = ("overview", "review")
PRODUCER_SECTIONS = ("triage", "plan", "implement")  # required once attempted, even if they failed


class ReportBuilder:
    def __init__(self, store, audit, run_state):
        self._store, self._audit, self._state = store, audit, run_state

    # --- facts and labels ----------------------------------------------------------------------

    def step_facts(self, agent):
        record = self._store.latest(agent)
        real = [e for e in self._audit.dispatches() if e["agent"] == agent and not is_caller_mistake(e)]
        return report.StepFacts(
            name=agent, attempted=record is not None or bool(real), artifact_exists=record is not None,
            artifact_fresh=self._state.is_fresh(agent), step_failed=bool(real) and not real[-1]["ok"],
            review=self._state.review_status(agent),
            test_failed=agent == "implement" and record is not None and not record.artifact["test_passed"],
        )

    def labels(self):
        return {agent: report.label_of(self.step_facts(agent)) for agent in STEPS}

    def required_sections(self):
        """The exact section set the writer must return, derived from artifacts and attempted steps."""
        needed = set(ALWAYS_REQUIRED)
        needed |= {s for s in PRODUCER_SECTIONS if self.step_facts(s).attempted}
        if self._store.latest("escalate") is not None:
            needed.add("escalation")
        return tuple(s for s in report.SECTION_ORDER if s in needed)

    # --- text ----------------------------------------------------------------------------------

    def draft(self, terminal_state, sections):
        return report.assemble_draft(terminal_state, self._state.human_required(), sections, self.labels())

    def failure_report(self, result):
        drafts = [draft_filename(r) for r in self._store.records(WRITER)]
        return report.render_failure(result, self._state.human_required(), self._state.human_reasons(),
                                     self.labels(), self._audit.failures(), drafts)
