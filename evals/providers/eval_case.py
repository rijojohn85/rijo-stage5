"""One eval case, parsed from a promptfoo test's `vars`.

The var names are the contract between the promptfoo yamls and the provider:
    agent      triage | plan | implement | escalate | writer | reviewer
    target     for reviewer: the artifact under review
    report     bug report text (optional; a neutral stand-in is used otherwise)
    triage, plan, implement, escalate    upstream artifacts, as JSON text or objects
    diff       for reviewer on implement: the recorded unified diff
    draft      for reviewer on writer: the report draft markdown
    sources    for reviewer on writer: the artifacts the draft summarises
    feedback   reviewer findings or stop reason handed to the agent
    first_pass revision cases: the producer's own earlier artifact, seeded so it sees it as previous_artifact
    terminal_state  for writer: the state the coordinator proposes (the real terminal-state check still runs)
    history    for writer: the run so far, in order: {"produce": <agent>, "artifact": {...}} and
               {"review": <target>, "verdict": ..., "findings": [...]} entries, seeded as saved steps
"""
import json
from dataclasses import dataclass, field

from harness.agents import ARTIFACT_CHAIN
from harness.store import REVIEWER

WRITER_TARGET = "writer"  # a reviewer target whose artifact is a rendered draft
NO_REPORT = "(no bug report text was supplied for this case)"


class CaseError(ValueError):
    """The test's vars do not describe a runnable case."""


def _as_list(value, name):
    if value in (None, ""):
        return ()
    parsed = value if isinstance(value, list) else _parse_json(value, name)
    if not isinstance(parsed, list):
        raise CaseError(f"var {name!r} is not a JSON list")
    return tuple(parsed)


def _parse_json(value, name):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CaseError(f"var {name!r} is not valid JSON: {exc}") from exc


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
    previous: dict | None = None  # the producer's own earlier artifact (revision cases)
    terminal_state: str | None = None
    history: tuple = ()

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
            previous=_as_object(case_vars["first_pass"], "first_pass") if case_vars.get("first_pass") else None,
            terminal_state=case_vars.get("terminal_state"), history=_as_list(case_vars.get("history"), "history"),
        )

    @staticmethod
    def _seeded_artifacts(agent, case_vars):
        """A producer starts from what comes before its own artifact; a reviewer from the whole chain."""
        if case_vars.get("target") == WRITER_TARGET:
            return _as_object(case_vars["sources"], "sources") if case_vars.get("sources") else {}
        chain = ARTIFACT_CHAIN.get(case_vars.get("target") or agent, ())
        names = chain if agent == REVIEWER else chain[:-1]
        return {n: _as_object(case_vars[n], n) for n in names if case_vars.get(n)}
