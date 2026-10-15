"""One eval case, parsed from a promptfoo test's `vars`.

The var names are the contract between the promptfoo yamls and the provider:
    agent      triage | plan | implement | escalate | reviewer
    target     for reviewer: the artifact under review
    report     bug report text (optional; a neutral stand-in is used otherwise)
    triage, plan, implement, escalate    upstream artifacts, as JSON text or objects
    diff       for reviewer on implement: the recorded unified diff
    draft      for reviewer on writer: the report draft markdown
    sources    for reviewer on writer: the artifacts the draft summarises
    feedback   reviewer findings or stop reason handed to the agent
"""
import json
from dataclasses import dataclass, field

from harness.agents import ARTIFACT_CHAIN
from harness.store import REVIEWER

WRITER_TARGET = "writer"  # reviewable only once the writer exists (step 5)
NO_REPORT = "(no bug report text was supplied for this case)"


class CaseError(ValueError):
    """The test's vars do not describe a runnable case."""


def _as_object(value, name):
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CaseError(f"var {name!r} is not a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CaseError(f"var {name!r} is not a JSON object")
    return parsed


@dataclass(frozen=True)
class EvalCase:
    agent: str
    target: str | None = None
    bug_report: str = NO_REPORT
    artifacts: dict = field(default_factory=dict)  # name -> artifact, in chain order
    diff: str = ""
    draft: str | None = None
    feedback: str | None = None

    @classmethod
    def from_vars(cls, case_vars):
        agent = case_vars.get("agent")
        if not agent:
            raise CaseError("var 'agent' is required")
        artifacts = cls._seeded_artifacts(agent, case_vars)
        return cls(
            agent=agent, target=case_vars.get("target"),
            bug_report=case_vars.get("report") or NO_REPORT,
            artifacts=artifacts, diff=case_vars.get("diff") or "",
            draft=case_vars.get("draft"), feedback=case_vars.get("feedback"),
        )

    @staticmethod
    def _seeded_artifacts(agent, case_vars):
        """A producer starts from what comes before its own artifact; a reviewer from the whole chain."""
        if case_vars.get("target") == WRITER_TARGET:
            return _as_object(case_vars["sources"], "sources") if case_vars.get("sources") else {}
        chain = ARTIFACT_CHAIN.get(case_vars.get("target") or agent, ())
        names = chain if agent == REVIEWER else chain[:-1]
        return {n: _as_object(case_vars[n], n) for n in names if case_vars.get(n)}
