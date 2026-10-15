import json

import pytest

from conftest import (ESCALATION, IMPLEMENT, PLAN, PLAN_FILE, TRIAGE, Env, json_reply, review_reply,
                      text_reply, tool_reply)
from harness.freshness import Freshness
from harness.llm import LlmError
from harness.results import ErrorCode
from harness.tool_loop import MAX_TURNS


def error_code(result):
    assert result["ok"] is False
    return result["error_code"]


def planned(env):
    """Run triage -> review -> plan -> review so implement is allowed."""
    env.reviewed("triage", TRIAGE)
    env.reviewed("plan", PLAN)


def test_good_reply_is_ok_and_saved(env):
    result = env.run("triage", json_reply(TRIAGE))
    assert result == {"ok": True, "agent": "triage", "summary": "bug, fix-now, escalation=False", "artifact": TRIAGE}
    assert json.loads((env.run_dir / "01-triage.json").read_text()) == TRIAGE


def test_not_json_is_rejected_and_raw_output_stays_out_of_the_result(env):
    result = env.run("triage", text_reply("Sure! Here is the triage: {oops"))
    assert error_code(result) == ErrorCode.NOT_JSON
    assert set(result) == {"ok", "agent", "error_code", "detail"}
    assert "oops" not in json.dumps(result)
    (rejected,) = env.rejected_files()
    assert json.loads(rejected.read_text())["raw_output"].startswith("Sure!")
    assert env.store.latest("triage") is None


def test_fenced_json_is_not_accepted(env):
    assert error_code(env.run("triage", text_reply("```json\n{}\n```"))) == ErrorCode.NOT_JSON


def test_schema_invalid(env):
    result = env.run("triage", json_reply({**TRIAGE, "decision": "maybe"}))
    assert error_code(result) == ErrorCode.SCHEMA_INVALID
    assert "decision" in result["detail"]
    assert len(env.rejected_files()) == 1


def test_data_loss_report_forces_escalation_even_if_triage_says_no(tmp_path):
    env = Env(tmp_path, bug_report="save_records destroys the file; we lost customer records")
    result = env.run("triage", json_reply(TRIAGE))
    assert result["ok"] and result["artifact"]["escalation"] is True
    assert result["artifact"]["decision"] == "defer"
    assert env.store.latest("triage").artifact["escalation"] is True


def test_escalated_triage_blocks_plan(tmp_path):
    env = Env(tmp_path, bug_report="data loss in production")
    env.reviewed("triage", TRIAGE)
    assert error_code(env.run("plan")) == ErrorCode.PRECONDITION_MISSING


def test_plan_scope_violation_from_claimed_changes(env):
    planned(env)
    bad = {**IMPLEMENT, "changed": [PLAN_FILE, "sandbox/demo-app/other.py"]}
    result = env.run("implement", json_reply(bad))
    assert error_code(result) == ErrorCode.PLAN_SCOPE_VIOLATION
    assert "other.py" in result["detail"]
    assert len(env.rejected_files()) == 1


def test_plan_scope_violation_from_unreported_git_changes(env):
    planned(env)
    env.sandbox.changed = [PLAN_FILE, "sandbox/demo-app/secret.py"]
    assert error_code(env.run("implement", json_reply(IMPLEMENT))) == ErrorCode.PLAN_SCOPE_VIOLATION


def test_implement_in_scope_is_ok(env):
    planned(env)
    env.sandbox.changed = [PLAN_FILE]
    assert env.run("implement", json_reply(IMPLEMENT))["ok"]


def test_review_binding_mismatch_wrong_hash(env):
    env.run("triage", json_reply(TRIAGE))
    result = env.run("reviewer", review_reply(reviewed_sha256="0" * 64), target="triage")
    assert error_code(result) == ErrorCode.REVIEW_BINDING_MISMATCH
    assert "reviewed_sha256" in result["detail"]


def test_review_binding_mismatch_wrong_target(env):
    env.run("triage", json_reply(TRIAGE))
    assert error_code(env.run("reviewer", review_reply(target="plan"), target="triage")) == ErrorCode.REVIEW_BINDING_MISMATCH


def test_review_binding_requires_diff_hash_for_implement(env):
    planned(env)
    env.run("implement", json_reply(IMPLEMENT))
    result = env.run("reviewer", review_reply(diff_sha256="f" * 64), target="implement")
    assert error_code(result) == ErrorCode.REVIEW_BINDING_MISMATCH
    assert "diff_sha256" in result["detail"]


def test_review_missing_binding_fields_fails_closed(env):
    env.run("triage", json_reply(TRIAGE))
    result = env.run("reviewer", json_reply({"verdict": "pass", "findings": []}), target="triage")
    assert error_code(result) == ErrorCode.REVIEW_BINDING_MISMATCH


def test_precondition_missing_makes_no_api_call(env):
    assert error_code(env.run("plan")) == ErrorCode.PRECONDITION_MISSING
    assert error_code(env.run("implement")) == ErrorCode.PRECONDITION_MISSING
    assert error_code(env.run("reviewer", target="triage")) == ErrorCode.PRECONDITION_MISSING
    assert env.llm.calls == []


def test_plan_needs_a_passing_review_of_triage(env):
    env.run("triage", json_reply(TRIAGE))
    assert error_code(env.run("plan")) == ErrorCode.PRECONDITION_MISSING
    env.run("reviewer", review_reply("revise", [{"category": "incorrect", "detail": "x"}]), target="triage")
    assert error_code(env.run("plan")) == ErrorCode.PRECONDITION_MISSING


def test_unreviewable_or_unknown_agents_are_refused(env):
    assert error_code(env.run("reviewer", target="writer")) == ErrorCode.PRECONDITION_MISSING
    assert error_code(env.run("writer")) == ErrorCode.PRECONDITION_MISSING
    assert error_code(env.run("nonsense")) == ErrorCode.PRECONDITION_MISSING


def test_stale_review_after_triage_is_replaced(env):
    planned(env)
    assert env.run("triage", json_reply({**TRIAGE, "rationale": "reworded"}))["ok"]
    # plan was built on the old triage, so its review and the plan itself no longer authorize implement
    assert error_code(env.run("implement")) == ErrorCode.PRECONDITION_MISSING
    assert error_code(env.run("reviewer", target="plan")) == ErrorCode.PRECONDITION_MISSING  # plan itself is stale
    assert error_code(env.run("plan")) == ErrorCode.PRECONDITION_MISSING  # new triage not yet reviewed


def test_review_goes_stale_when_diff_changes(env):
    planned(env)
    env.run("implement", json_reply(IMPLEMENT))
    assert env.run("reviewer", review_reply(), target="implement")["ok"]
    freshness = Freshness(env.store, env.sandbox)
    review = env.store.latest("reviewer", "implement")
    assert freshness.review_is_fresh(review)
    env.sandbox.diff_text = "a different diff"
    assert not freshness.review_is_fresh(review)


def test_revision_cap_allows_initial_plus_one_replacement(env):
    assert env.run("triage", json_reply(TRIAGE))["ok"]
    assert env.run("triage", json_reply(TRIAGE), feedback="try again")["ok"]
    result = env.run("triage")
    assert error_code(result) == ErrorCode.REVISION_CAP_REACHED
    assert len(env.llm.calls) == 2


def test_failed_attempts_do_not_consume_the_allowance(env):
    env.run("triage", text_reply("nope"))
    env.run("triage", text_reply("nope again"))
    assert env.run("triage", json_reply(TRIAGE))["ok"]
    assert env.run("triage", json_reply(TRIAGE))["ok"]


def test_api_error(env):
    result = env.run("triage", LlmError("RateLimitError: slow down"))
    assert error_code(result) == ErrorCode.API_ERROR
    assert "slow down" in result["detail"]
    assert env.rejected_files() == []  # no model output exists to reject


def test_tool_budget_exceeded(env):
    loop_forever = [tool_reply("read_file", {"path": "sandbox/missing.py"}, f"c{i}") for i in range(MAX_TURNS + 1)]
    result = env.run("triage", *loop_forever)
    assert error_code(result) == ErrorCode.TOOL_BUDGET_EXCEEDED
    assert len(env.llm.calls) == MAX_TURNS
    assert env.audit.entries()[-1]["input_tokens"] == 100 * MAX_TURNS  # every loop call is costed


def test_tool_results_are_fed_back_to_the_model(env):
    (env.root / "sandbox" / "calc.py").write_text("def add(a, b): return a - b\n")
    result = env.run("triage", tool_reply("read_file", {"path": "sandbox/calc.py"}), json_reply(TRIAGE))
    assert result["ok"]
    tool_message = env.llm.calls[1]["messages"][-1]
    assert tool_message["role"] == "tool" and "a - b" in tool_message["content"]


def test_unlisted_tool_call_is_refused_and_not_executed(env):
    target = env.root / "sandbox" / "calc.py"
    target.write_text("original")
    attack = tool_reply("edit_file", {"path": "sandbox/calc.py", "old_text": "original", "new_text": "pwned"})
    result = env.run("triage", attack, json_reply(TRIAGE))  # triage only has read_file
    assert result["ok"]
    assert target.read_text() == "original"
    refusal = json.loads(env.llm.calls[1]["messages"][-1]["content"])
    assert refusal["error_code"] == ErrorCode.EXTRA_TOOL_CALL
    assert env.audit.entries()[-1]["refused_tool_calls"] == 1


def test_model_cannot_override_the_sandbox_root_through_tool_arguments(env):
    escape = tool_reply("read_file", {"path": "/etc/hostname", "root": "/"})
    env.run("triage", escape, json_reply(TRIAGE))
    refusal = json.loads(env.llm.calls[1]["messages"][-1]["content"])
    assert refusal["error_code"] == "BAD_ARGUMENT" and "root" in refusal["detail"]


@pytest.mark.parametrize("agent,tools", [
    ("triage", ["read_file"]), ("escalate", []), ("reviewer", ["read_file"]),
])
def test_helper_receives_only_its_manifest_tools(env, agent, tools):
    env.run("triage", json_reply(TRIAGE))
    if agent == "reviewer":
        env.run(agent, review_reply(), target="triage")
    elif agent == "escalate":
        env.run(agent, json_reply(ESCALATION))
    sent = env.llm.calls[-1]["tools"]
    assert [t["function"]["name"] for t in sent] == tools


def test_implement_receives_exactly_its_three_tools(env):
    planned(env)
    env.run("implement", json_reply(IMPLEMENT))
    assert [t["function"]["name"] for t in env.llm.calls[-1]["tools"]] == ["read_file", "edit_file", "run_test"]


def test_every_dispatch_is_a_fresh_conversation(env):
    env.run("triage", tool_reply("read_file", {"path": "sandbox/x"}), json_reply(TRIAGE))
    env.run("triage", json_reply(TRIAGE), feedback="again")
    first_call, _, second_dispatch_call = env.llm.calls
    assert [m["role"] for m in first_call["messages"]] == ["system", "user"]
    assert [m["role"] for m in second_dispatch_call["messages"]] == ["system", "user"]
    assert "read_file" not in json.dumps(second_dispatch_call["messages"])


def test_reviewer_prompt_contains_artifact_chain_and_diff(env):
    planned(env)
    env.run("implement", json_reply(IMPLEMENT))
    env.run("reviewer", review_reply(), target="implement")
    body = json.loads(env.llm.calls[-1]["messages"][1]["content"])
    assert set(body["artifacts"]) == {"triage", "plan", "implement"}
    assert body["diff"] and body["review_target"] == "implement"


def test_escalate_works_without_triage_and_sees_earlier_errors(env):
    env.run("triage", text_reply("garbage"))
    assert env.run("escalate", json_reply(ESCALATION))["ok"]
    body = json.loads(env.llm.calls[-1]["messages"][1]["content"])
    assert body["earlier_errors"][0]["error_code"] == ErrorCode.NOT_JSON


def test_audit_line_for_ok_dispatch(env):
    env.run("triage", json_reply(TRIAGE))
    (line,) = env.audit.entries()
    assert line["correlation_id"] == "20261015-111500" and line["step"] == 1
    assert line["agent"] == "triage" and line["target"] is None and line["ok"] is True
    assert line["model"] == "accounts/fireworks/models/deepseek-v4p1-flash"
    assert (line["input_tokens"], line["output_tokens"]) == (100, 50)
    assert line["cost_usd"] == pytest.approx((100 * 0.30 + 50 * 1.20) / 1_000_000)
    assert line["artifact"] == "01-triage.json" and len(line["sha256"]) == 64
    assert line["ts"].startswith("2026-10-15T11:15")


def test_audit_lines_for_rejected_dispatches_and_step_numbers_increment(env):
    env.run("triage", text_reply("nope"))
    env.run("plan")  # precondition rejection, no API call
    env.run("triage", LlmError("down"))
    lines = env.audit.entries()
    assert [(l["step"], l["ok"], l["error_code"]) for l in lines] == [
        (1, False, "NOT_JSON"), (2, False, "PRECONDITION_MISSING"), (3, False, "API_ERROR")]
    assert lines[1]["input_tokens"] == 0 and lines[1]["cost_usd"] == 0
    assert all(l["correlation_id"] == "20261015-111500" for l in lines)


def test_review_artifact_is_named_after_its_target(env):
    env.reviewed("triage", TRIAGE)
    assert (env.run_dir / "02-reviewer-triage.json").exists()
