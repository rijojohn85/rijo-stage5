"""Scripted sub-agents for the coordinator governance suite: only the coordinator's reasoning is tested.

`CannedHelpers` is the LLM client the dispatcher gets for every sub-agent model. It never calls a
model: it works out which agent is asking from the system prompt, then replays that agent's
script. The dispatcher, its checks, the review binding, the writer checks and the audit run for
real, so a canned reply is validated exactly like a live one.

A script maps an agent to the replies it should give, in call order (the last reply repeats):
    {"screen": [<artifact>, ...], "triage": [...], "plan": [...], "implement": [...], "escalate": [...],
     "reviewer": {"<target>": [{"verdict": "pass", "findings": []}, ...]}}
A string reply is returned verbatim, which is how a case scripts invalid output ("not json").
Missing entries fall back to DEFAULTS; the writer always answers with prose for the sections asked for.
"""
import json

from harness.llm import LlmReply, Usage
from harness.manifest import load_agent_specs
from harness.prompts import PromptLibrary
from harness.store import REVIEWER, WRITER

PLAN_FILE = "sandbox/demo-app/calc.py"
PLAN_TEST_FILE = "sandbox/demo-app/test_calc_empty.py"
DEFAULTS = {
    "screen": [{"verdict": "clean", "evidence": [], "reason": "an ordinary bug report"}],
    "triage": [{"classification": "bug", "decision": "fix-now", "root_cause": "calc.py: average() divides by len([])",
                "rationale": "a small local defect with a documented expected result", "escalation": False}],
    "plan": [{"files": [PLAN_FILE, PLAN_TEST_FILE], "steps": ["return 0.0 for an empty list"],
              "test_files": [PLAN_TEST_FILE]}],
    "implement": [{"changed": [PLAN_FILE], "summary": "average() returns 0.0 for an empty list", "test_passed": True}],
    "escalate": [{"reason": "the review found a dangerous problem", "question": "how should we proceed?",
                  "options": ["stop and review by hand", "continue with a backup"], "risk_if_wrong": "lost records"}],
}
PASSING_REVIEW = {"verdict": "pass", "findings": []}
NO_USAGE = Usage(0, 0)


class ScriptError(ValueError):
    """The case's script does not describe a runnable case."""


def _next(replies, counter):
    index = min(counter, len(replies) - 1)
    return replies[index]


def _text(reply):
    return reply if isinstance(reply, str) else json.dumps(reply)


class CannedHelpers:
    def __init__(self, script):
        self._script = {**DEFAULTS, **script}
        self._counts = {}
        self._agent_by_prompt = self._index_prompts()

    @staticmethod
    def _index_prompts():
        library = PromptLibrary()
        return {library.system_prompt(spec.workflow): spec.name for spec in load_agent_specs().values()}

    def chat(self, model, messages, tools):
        agent = self._agent_by_prompt.get(messages[0]["content"])
        if agent is None:
            raise ScriptError("a sub-agent was called with a system prompt that belongs to no manifest agent")
        user = json.loads(messages[1]["content"])
        reply = {REVIEWER: self._review, WRITER: self._write}.get(agent, self._produce)(agent, user)
        return LlmReply(content=_text(reply), usage=NO_USAGE)

    def _count(self, key):
        number = self._counts.get(key, 0)
        self._counts[key] = number + 1
        return number

    def _produce(self, agent, user):
        return _next(self._script[agent], self._count(agent))

    def _review(self, agent, user):
        """A scripted verdict for this target, bound to exactly what the dispatcher asked to be echoed."""
        target = user["review_target"]
        replies = self._script.get(REVIEWER, {}).get(target, [PASSING_REVIEW])
        scripted = _next(replies, self._count((REVIEWER, target)))
        if isinstance(scripted, str):
            return scripted
        return {**scripted, **user["echo_these_fields_in_your_reply"]}

    def _write(self, agent, user):
        return {"sections": {key: f"Thank you for the report. Here is what happened under {key}."
                             for key in user["required_sections"]}}


class SplitClient:
    """One LLM client for both levels: the real coordinator model, and canned replies for every sub-agent."""

    def __init__(self, coordinator_model, coordinator_llm, helpers):
        self._model, self._coordinator_llm, self._helpers = coordinator_model, coordinator_llm, helpers

    def chat(self, model, messages, tools):
        client = self._coordinator_llm if model == self._model else self._helpers
        return client.chat(model, messages, tools)
