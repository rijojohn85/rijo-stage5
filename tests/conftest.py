"""Shared fakes for dispatcher tests. Nothing here touches the network or costs money."""
import json
from datetime import datetime, timezone

import pytest

from harness.audit import AuditLog
from harness.llm import LlmReply, ToolCall, Usage
from harness.store import ArtifactStore
from harness.wiring import build_dispatcher

FIXED_TIME = datetime(2026, 10, 15, 11, 15, tzinfo=timezone.utc)
CORRELATION_ID = "20261015-111500"
TEST_USAGE = Usage(input_tokens=100, output_tokens=50)
PLAN_FILE = "sandbox/demo-app/calc.py"

TRIAGE = {"classification": "bug", "decision": "fix-now", "root_cause": "calc.py:add subtracts",
          "rationale": "small local fix", "escalation": False}
PLAN = {"files": [PLAN_FILE], "steps": ["fix add"], "test": "python3 -m pytest sandbox/demo-app"}
IMPLEMENT = {"changed": [PLAN_FILE], "summary": "fixed add", "test_passed": True}
ESCALATION = {"reason": "data loss", "question": "proceed?", "options": ["yes", "no"], "risk_if_wrong": "lost data"}


def text_reply(content, usage=TEST_USAGE):
    return LlmReply(content=content, usage=usage)


def json_reply(obj, usage=TEST_USAGE):
    return text_reply(json.dumps(obj), usage)


def tool_reply(name, arguments, call_id="call-1"):
    return LlmReply(content=None, tool_calls=(ToolCall(call_id, name, json.dumps(arguments)),), usage=TEST_USAGE)


def review_reply(verdict="pass", findings=(), **overrides):
    """A reviewer that echoes the binding the dispatcher asked for (read from its own prompt)."""
    def build(messages):
        echoed = json.loads(messages[1]["content"])["echo_these_fields_in_your_reply"]
        return json_reply({"verdict": verdict, "findings": list(findings), **echoed, **overrides})
    return build


class FakeLlm:
    """Scripted replies; an item may be a reply, an exception to raise, or a callable(messages)."""

    def __init__(self, *script):
        self._script = list(script)
        self.calls = []

    def chat(self, model, messages, tools):
        self.calls.append({"model": model, "messages": [dict(m) for m in messages], "tools": tools})
        item = self._script.pop(0)
        if callable(item):
            item = item(messages)
        if isinstance(item, Exception):
            raise item
        return item


class FakeSandbox:
    def __init__(self, changed=(), diff="diff --git a/x b/x\n"):
        self.changed, self.diff_text = list(changed), diff

    def changed_files(self):
        return list(self.changed)

    def diff(self):
        return self.diff_text


class Env:
    """A dispatcher wired to temp dirs and a fake LLM; script replies with env.llm."""

    def __init__(self, tmp_path, bug_report="calc.add returns the wrong sum"):
        self.run_dir = tmp_path / "runs" / CORRELATION_ID
        self.root = tmp_path / "project"
        (self.root / "sandbox").mkdir(parents=True)
        self.bug_report_path = tmp_path / "bug.md"
        self.bug_report_path.write_text(bug_report, encoding="utf-8")
        self.sandbox = FakeSandbox()
        self.store = ArtifactStore(self.run_dir)
        self.audit = AuditLog(self.run_dir / "audit.jsonl")
        self.llm = FakeLlm()

    def script(self, *replies):
        self.llm._script.extend(replies)

    def dispatcher(self):
        return build_dispatcher(
            llm=self.llm, run_dir=self.run_dir, tool_root=self.root, sandbox=self.sandbox,
            bug_report_path=self.bug_report_path, correlation_id=CORRELATION_ID, clock=lambda: FIXED_TIME,
        )

    def run(self, agent, *reply, target=None, feedback=None):
        """Script `reply` then dispatch once."""
        self.script(*reply)
        return self.dispatcher().dispatch(agent, target=target, feedback=feedback)

    def reviewed(self, agent, artifact):
        """Produce an artifact and a passing review of it."""
        assert self.run(agent, json_reply(artifact))["ok"]
        assert self.run("reviewer", review_reply(), target=agent)["ok"]

    def rejected_files(self):
        return sorted((self.run_dir / "rejected").glob("*.json"))


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)
