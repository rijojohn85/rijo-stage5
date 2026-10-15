"""Coordinator loop with a fake LLM: nothing here touches the network."""
import io
import json
from datetime import datetime, timezone

import pytest

from conftest import (CORRELATION_ID, ESCALATION, FIXED_TIME, IMPLEMENT, PLAN, PLAN_FILE, TRIAGE, FakeLlm,
                      FakeSandbox, json_reply, review_reply, text_reply, tool_reply, writer_reply)
from harness.audit import AuditLog
from harness.coordinator import FAILED_TURN_BUDGET, MAX_TURNS
from harness.coordinator_wiring import build_coordinator
from harness.freshness import Freshness
from harness.llm import LlmReply, ToolCall, Usage
from harness.manifest import load_coordinator_spec
from harness.run import run_bug
from harness.run_log import RunLog
from harness.run_state import RunState
from harness.store import ArtifactStore

COORDINATOR_MODEL = load_coordinator_spec().model
BUG = "calc.add returns the wrong sum"
REVISE = [{"category": "incorrect", "detail": "wrong file"}]


class SplitLlm:
    """One client for both levels: the coordinator model and the sub-agent model get separate scripts."""

    def __init__(self, coordinator, helpers):
        self.coordinator, self.helpers = FakeLlm(*coordinator), FakeLlm(*helpers)

    def chat(self, model, messages, tools):
        return (self.coordinator if model == COORDINATOR_MODEL else self.helpers).chat(model, messages, tools)


_ids = iter(range(1, 10_000))


def launch(name, **arguments):
    return tool_reply(name, arguments, call_id=f"call-{next(_ids)}")


def reviewing(target):
    return launch("launch_reviewer", target=target)


HAPPY_COORDINATOR = [
    launch("launch_triage"), reviewing("triage"), launch("launch_plan"), reviewing("plan"),
    launch("launch_implement"), reviewing("implement"),
    launch("launch_writer", terminal_state="VERIFIED"), reviewing("writer"),
]
HAPPY_HELPERS = [json_reply(TRIAGE), review_reply(), json_reply(PLAN), review_reply(),
                 json_reply(IMPLEMENT), review_reply(), writer_reply(), review_reply()]


def build(tmp_path, coordinator, helpers, *, log=None, max_turns=MAX_TURNS, sandbox=None):
    run_dir = tmp_path / "runs" / CORRELATION_ID
    root = tmp_path / "project"
    (root / "sandbox").mkdir(parents=True)
    bug = tmp_path / "bug.md"
    bug.write_text(BUG, encoding="utf-8")
    llm = SplitLlm(coordinator, helpers)
    sandbox = sandbox or FakeSandbox(changed=[PLAN_FILE])
    built = build_coordinator(llm=llm, run_dir=run_dir, tool_root=root, sandbox=sandbox, bug_report_path=bug,
                              correlation_id=CORRELATION_ID, clock=lambda: FIXED_TIME, log=log, max_turns=max_turns)
    return built, llm, AuditLog(run_dir / "audit.jsonl"), run_dir


def tool_results(llm):
    """Every tool result the coordinator was shown, in order."""
    last = llm.coordinator.calls[-1]["messages"]
    return [json.loads(m["content"]) for m in last if m["role"] == "tool"]


def test_happy_path_ends_verified_with_every_producer_reviewed(tmp_path):
    coordinator, llm, audit, run_dir = build(tmp_path, HAPPY_COORDINATOR, HAPPY_HELPERS)
    outcome = coordinator.run(BUG)
    assert outcome.result == "VERIFIED" and not outcome.human_required
    dispatches = [(e["agent"], e["target"]) for e in audit.dispatches()]
    assert dispatches == [("triage", None), ("reviewer", "triage"), ("plan", None), ("reviewer", "plan"),
                          ("implement", None), ("reviewer", "implement"), ("writer", None), ("reviewer", "writer")]
    assert [e["step"] for e in audit.dispatches()] == list(range(1, 9))  # coordinator turns never shift NN
    assert sum(e["agent"] == "coordinator" for e in audit.entries()) == 8
    assert {e["correlation_id"] for e in audit.entries()} == {CORRELATION_ID}
    assert (run_dir / "audit.jsonl").exists()
    assert all(e["model"].endswith("deepseek-v4p1-flash") for e in audit.dispatches())  # the writer is a real model call
    draft = (run_dir / "07-writer-draft.md").read_text()
    assert outcome.report_path == run_dir / "report.md" and outcome.report_path.read_text() == draft
    assert draft.startswith("# Result: VERIFIED") and "## Implement: PASS" in draft


def test_coordinator_is_offered_only_launch_tools(tmp_path):
    coordinator, llm, _, _ = build(tmp_path, HAPPY_COORDINATOR, HAPPY_HELPERS)
    coordinator.run(BUG)
    offered = {t["function"]["name"] for t in llm.coordinator.calls[0]["tools"]}
    assert offered == {"launch_triage", "launch_plan", "launch_implement", "launch_reviewer",
                       "launch_escalate", "launch_writer"}
    assert llm.coordinator.calls[0]["messages"][0]["role"] == "system"


def test_revision_cap_then_escalation_ends_human_punch_out(tmp_path):
    coordinator_script = [
        launch("launch_triage"), reviewing("triage"), launch("launch_plan"), reviewing("plan"),
        launch("launch_plan", feedback="wrong file"), reviewing("plan"),
        launch("launch_plan", feedback="again"),               # refused: allowance spent
        launch("launch_escalate", feedback="plan revised twice"), reviewing("escalate"),
        launch("launch_writer", terminal_state="HUMAN_PUNCH_OUT"), reviewing("writer"),
    ]
    helpers = [json_reply(TRIAGE), review_reply(), json_reply(PLAN), review_reply("revise", REVISE),
               json_reply(PLAN), review_reply("revise", REVISE), json_reply(ESCALATION), review_reply(),
               writer_reply(), review_reply()]
    coordinator, llm, audit, run_dir = build(tmp_path, coordinator_script, helpers)
    outcome = coordinator.run(BUG)
    assert outcome.result == "HUMAN_PUNCH_OUT" and outcome.human_required
    report = (run_dir / "report.md").read_text()
    assert "**Human decision required**" in report and "## Escalation: AWAITING HUMAN (review: pass)" in report
    assert "## Plan: FAIL (revise)" in report
    assert any("plan revision allowance exhausted" in r for r in outcome.human_reasons)
    refused = [e for e in audit.dispatches() if e["error_code"] == "REVISION_CAP_REACHED"]
    assert len(refused) == 1 and refused[0]["agent"] == "plan"
    escalate_call = [c for c in llm.helpers.calls if "earlier_errors" in c["messages"][1]["content"]][0]
    assert "plan revised twice" in escalate_call["messages"][1]["content"]  # stop reason travels as feedback


def test_turn_bound_stops_the_run(tmp_path):
    spin = [reviewing("triage")] * 3          # precondition errors cost nothing and never finish
    coordinator, llm, audit, _ = build(tmp_path, spin, [], max_turns=3)
    outcome = coordinator.run(BUG)
    assert outcome.result == FAILED_TURN_BUDGET and outcome.turns == 3
    assert len(llm.coordinator.calls) == 3
    assert sum(e["agent"] == "coordinator" for e in audit.entries()) == 3


def test_default_turn_bound_is_32():
    assert MAX_TURNS == 32


def test_stale_artifacts_are_reported_and_block_completion(tmp_path):
    script = [
        launch("launch_triage"), reviewing("triage"), launch("launch_plan"), reviewing("plan"),
        launch("launch_triage", feedback="revise"),           # triage replaced -> plan is now stale
        launch("launch_writer", terminal_state="VERIFIED"),    # illegal: nothing is fresh
        reviewing("writer"),                                   # only here to show the previous result
    ]
    helpers = [json_reply(TRIAGE), review_reply(), json_reply(PLAN), review_reply(),
               json_reply({**TRIAGE, "rationale": "reworded"})]
    coordinator, llm, audit, _ = build(tmp_path, script, helpers, max_turns=7)
    assert coordinator.run(BUG).result == FAILED_TURN_BUDGET
    results = tool_results(llm)
    after_retriage = results[4]["run_state"]["artifacts"]
    assert after_retriage["plan"]["fresh"] is False and after_retriage["triage"]["review"] == "stale"
    assert after_retriage["triage"]["replacements_left"] == 0
    assert results[5]["ok"] is False and results[5]["error_code"] == "PRECONDITION_MISSING"


def test_schema_error_reaches_the_coordinator_as_a_structured_error(tmp_path):
    bad = json_reply({**TRIAGE, "decision": "maybe"})
    coordinator, llm, audit, run_dir = build(tmp_path, [launch("launch_triage")], [bad], max_turns=1)
    coordinator.run(BUG)
    # one turn only: read the error from the stored conversation of the (single) coordinator call
    entry = audit.dispatches()[0]
    assert entry["error_code"] == "SCHEMA_INVALID" and not entry["ok"]
    assert list((run_dir / "rejected").glob("*.json"))


def test_coordinator_sees_schema_error_without_raw_output(tmp_path):
    bad = text_reply("garbage {oops")
    script = [launch("launch_triage"), reviewing("triage")]
    coordinator, llm, _, _ = build(tmp_path, script, [bad], max_turns=2)
    coordinator.run(BUG)
    first = tool_results(llm)[0]
    assert first["ok"] is False and first["error_code"] == "NOT_JSON"
    assert "oops" not in json.dumps(first) and set(first) == {"ok", "agent", "error_code", "detail", "run_state"}


def test_premature_done_is_rejected_and_the_run_continues(tmp_path):
    script = [text_reply("All done, VERIFIED!"), text_reply("Really done.")]
    coordinator, llm, audit, _ = build(tmp_path, script, [], max_turns=2)
    outcome = coordinator.run(BUG)
    assert outcome.result == FAILED_TURN_BUDGET
    second_call = llm.coordinator.calls[1]["messages"]
    assert second_call[-1]["role"] == "user" and "Not finished" in second_call[-1]["content"]
    assert not audit.dispatches()


def test_a_writer_draft_without_evidence_is_refused(tmp_path):
    script = [launch("launch_writer", terminal_state="VERIFIED"), launch("launch_writer", terminal_state="DEFERRED"),
              launch("launch_writer", terminal_state="HUMAN_PUNCH_OUT"), reviewing("writer")]
    coordinator, llm, audit, _ = build(tmp_path, script, [], max_turns=4)
    assert coordinator.run(BUG).result == FAILED_TURN_BUDGET
    codes = [e["error_code"] for e in audit.dispatches()]
    assert codes == ["PRECONDITION_MISSING"] * 4


def test_only_the_first_tool_call_of_a_turn_runs(tmp_path):
    two = LlmReply(content=None, usage=Usage(1, 1), tool_calls=(
        ToolCall("a", "launch_triage", "{}"), ToolCall("b", "launch_plan", "{}")))
    coordinator, llm, audit, _ = build(tmp_path, [two], [json_reply(TRIAGE)], max_turns=1)
    coordinator.run(BUG)
    assert [e["agent"] for e in audit.dispatches()] == ["triage"]
    coordinator_entry = [e for e in audit.entries() if e["agent"] == "coordinator"][0]
    assert coordinator_entry["refused_tool_calls"] == 1


def test_bad_launch_arguments_and_unknown_tools_are_structured_errors(tmp_path):
    script = [launch("launch_reviewer", target="nonsense"), launch("read_file", path="x"), launch("launch_writer"),
              reviewing("writer")]
    coordinator, llm, audit, _ = build(tmp_path, script, [], max_turns=4)
    coordinator.run(BUG)
    results = tool_results(llm)
    assert [r["error_code"] for r in results] == ["BAD_ARGUMENT", "EXTRA_TOOL_CALL", "BAD_ARGUMENT"]
    assert [e["target"] for e in audit.dispatches()] == ["writer"]  # only the well-formed launch was dispatched


def test_defer_path_finishes_deferred(tmp_path):
    script = [launch("launch_triage"), reviewing("triage"),
              launch("launch_writer", terminal_state="DEFERRED"), reviewing("writer")]
    helpers = [json_reply({**TRIAGE, "decision": "defer"}), review_reply(), writer_reply(), review_reply()]
    coordinator, _, audit, run_dir = build(tmp_path, script, helpers)
    assert coordinator.run(BUG).result == "DEFERRED"
    report = (run_dir / "report.md").read_text()
    assert "## Triage: PASS" in report and "- Plan: NOT RUN" in report and "## Plan" not in report.split("Review status")[0]


def test_finalization_rechecks_the_state_when_evidence_goes_stale(tmp_path):
    defer = {**TRIAGE, "decision": "defer"}
    script = [launch("launch_triage"), reviewing("triage"), launch("launch_writer", terminal_state="DEFERRED"),
              launch("launch_triage", feedback="again"), reviewing("writer")]
    helpers = [json_reply(defer), review_reply(), writer_reply(), json_reply({**defer, "rationale": "reworded"})]
    coordinator, llm, audit, run_dir = build(tmp_path, script, helpers, max_turns=5)
    assert coordinator.run(BUG).result == FAILED_TURN_BUDGET   # triage changed under the draft, so it is stale
    assert audit.dispatches()[-1]["error_code"] == "PRECONDITION_MISSING"  # the stale draft cannot even be reviewed
    assert (run_dir / "report.md").read_text().startswith("# Result: FAILED_TURN_BUDGET")


def make_state(env):
    return RunState(env.store, env.audit, Freshness(env.store, env.sandbox))


def test_human_required_from_a_triage_escalation(env):
    env.run("triage", json_reply({**TRIAGE, "escalation": True, "decision": "defer"}))
    assert make_state(env).human_required()


def test_a_dangerous_review_keeps_human_required_after_a_later_pass(env):
    env.reviewed("triage", TRIAGE)
    env.run("reviewer", review_reply("escalate", [{"category": "dangerous", "detail": "deletes data"}]),
            target="triage")
    env.run("triage", json_reply(TRIAGE), feedback="redo")
    env.run("reviewer", review_reply(), target="triage")
    state = make_state(env)
    assert state.review_status("triage") == "pass" and state.human_required()


def test_one_error_then_success_is_recovered_but_two_in_a_row_need_a_human(env):
    env.run("triage", text_reply("nope"))
    env.run("triage", json_reply(TRIAGE))
    assert not make_state(env).human_required()
    env.run("reviewer", text_reply("nope"), target="triage")
    env.run("reviewer", text_reply("nope"), target="triage")
    assert any("failed 2 times" in r for r in make_state(env).human_reasons())


def test_a_challenged_artifact_with_no_replacement_left_needs_a_human(env):
    env.run("triage", json_reply(TRIAGE))
    env.run("triage", json_reply(TRIAGE), feedback="x")
    assert not make_state(env).human_required()
    env.run("reviewer", review_reply("revise", REVISE), target="triage")
    state = make_state(env)
    assert state.replacements_left("triage") == 0 and state.human_required()


def test_live_log_lines_appear_in_stderr_and_run_log(tmp_path):
    bug = tmp_path / "bug.md"
    bug.write_text(BUG, encoding="utf-8")
    (tmp_path / "project" / "sandbox").mkdir(parents=True)
    stderr = io.StringIO()
    llm = SplitLlm(HAPPY_COORDINATOR, HAPPY_HELPERS)
    run_dir, outcome = run_bug(bug, llm=llm, runs_dir=tmp_path / "runs", tool_root=tmp_path / "project",
                               sandbox=FakeSandbox(changed=[PLAN_FILE]), clock=lambda: FIXED_TIME, stderr=stderr)
    assert outcome.result == "VERIFIED"
    on_disk = (run_dir / "run.log").read_text(encoding="utf-8")
    assert on_disk == stderr.getvalue()
    for expected in ("run-start", "correlation_id=20261015-111500", "turn         n=1/32", "launch       agent=triage",
                     "attempt=1", "ok           agent=triage artifact=01-triage.json", "verdict      target=plan verdict=pass findings=0",
                     "terminal     state=VERIFIED"):
        assert expected in on_disk, expected
    assert (run_dir / "00-report.md").read_text() == BUG and (run_dir / "audit.jsonl").exists()
    assert "FIREWORKS" not in on_disk


def test_log_reports_cap_hit_stale_and_human_required(tmp_path):
    stream = io.StringIO()
    script = [launch("launch_triage"), reviewing("triage"), launch("launch_triage", feedback="x"),
              launch("launch_triage", feedback="y")]
    helpers = [json_reply(TRIAGE), review_reply("revise", REVISE), json_reply(TRIAGE)]
    log = RunLog([stream], lambda: FIXED_TIME)
    coordinator, *_ = build(tmp_path, script, helpers, log=log, max_turns=4)
    coordinator.run(BUG)
    text = stream.getvalue()
    assert "cap-hit      agent=triage" in text and "run_state    human_required=True" in text
    assert "error        agent=triage error_code=REVISION_CAP_REACHED" in text


def logged_costs(text):
    """cost_usd on coordinator `turn` lines and on helper `ok` lines: together they are every model call."""
    return [float(line.split("cost_usd=")[1].split()[0]) for line in text.splitlines()
            if (line.split() or [""])[1:2] in (["turn"], ["ok"]) and "cost_usd=" in line]


def test_turn_log_lines_carry_cost_and_all_logged_costs_add_up_to_the_total(tmp_path):
    stream = io.StringIO()
    coordinator, *_ = build(tmp_path, HAPPY_COORDINATOR, HAPPY_HELPERS, log=RunLog([stream], lambda: FIXED_TIME))
    outcome = coordinator.run(BUG)
    text = stream.getvalue()
    assert "turn         n=1/32 tokens=100+50 cost_usd=0.00036" in text
    assert len(logged_costs(text)) == 16 and outcome.cost_usd > 0
    assert round(sum(logged_costs(text)), 6) == outcome.cost_usd
    assert f"terminal     state=VERIFIED turns=8 human_required=False cost_usd={outcome.cost_usd}" in text


def test_turn_budget_failure_publishes_the_deterministic_failure_report(tmp_path):
    stream = io.StringIO()
    coordinator, _, _, run_dir = build(tmp_path, [reviewing("triage")] * 2, [], max_turns=2,
                                       log=RunLog([stream], lambda: FIXED_TIME))
    outcome = coordinator.run(BUG)
    report = (run_dir / "report.md").read_text()
    assert outcome.report_path == run_dir / "report.md" and report.startswith("# Result: FAILED_TURN_BUDGET")
    assert "No accepted writer artifact exists" in report and "## Triage: NOT RUN" in report
    assert "published    kind=failure-report" in stream.getvalue()


def test_coordinator_api_failure_publishes_the_failure_report(tmp_path):
    from harness.llm import LlmError
    coordinator, _, _, run_dir = build(tmp_path, [LlmError("boom")], [], max_turns=3)
    assert coordinator.run(BUG).result == "FAILED_COORDINATOR_API"
    assert (run_dir / "report.md").read_text().startswith("# Result: FAILED_COORDINATOR_API")


def test_the_published_report_is_the_reviewed_draft_byte_for_byte(tmp_path):
    from harness.hashing import sha256_text
    coordinator, _, audit, run_dir = build(tmp_path, HAPPY_COORDINATOR, HAPPY_HELPERS)
    coordinator.run(BUG)
    draft = (run_dir / "07-writer-draft.md").read_text()
    assert sha256_text((run_dir / "report.md").read_text()) == sha256_text(draft)
    review = json.loads((run_dir / "08-reviewer-writer.json").read_text())
    assert review["draft_sha256"] == sha256_text(draft) and review["verdict"] == "pass"


def test_a_failed_draft_review_never_publishes_the_draft(tmp_path):
    script = HAPPY_COORDINATOR[:-1] + [reviewing("writer")]
    helpers = HAPPY_HELPERS[:-1] + [review_reply("revise", REVISE)]
    coordinator, _, _, run_dir = build(tmp_path, script, helpers, max_turns=8)
    outcome = coordinator.run(BUG)
    assert outcome.result == FAILED_TURN_BUDGET
    report = (run_dir / "report.md").read_text()
    assert report.startswith("# Result: FAILED_TURN_BUDGET") and "07-writer-draft.md" in report
    assert report != (run_dir / "07-writer-draft.md").read_text()
