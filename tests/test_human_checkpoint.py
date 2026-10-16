"""While a human must decide, plan and implement cannot run; escalate and the reviewer still can."""
from conftest import ESCALATION, IMPLEMENT, PLAN, PLAN_FILE, TRIAGE, json_reply, review_reply

DANGEROUS = [{"category": "dangerous", "detail": "the fix deletes customer records"}]
REVISE = [{"category": "incorrect", "detail": "wrong file"}]


def human_required_env(env):
    """Triage, plan and implement all reviewed; the implement review says escalate."""
    env.reviewed("triage", TRIAGE)
    env.reviewed("plan", PLAN)
    env.sandbox.changed = [PLAN_FILE]
    assert env.run("implement", json_reply(IMPLEMENT))["ok"]
    assert env.run("reviewer", review_reply("escalate", DANGEROUS), target="implement")["ok"]
    return env


def assert_refused_for_human(env, result):
    assert not result["ok"]
    assert result["error_code"] == "PRECONDITION_MISSING"
    assert "a human is required" in result["detail"]


def test_implement_is_refused_while_a_human_is_required(env):
    human_required_env(env)
    calls, records, changed = len(env.llm.calls), len(env.store.records("implement")), list(env.sandbox.changed)
    assert_refused_for_human(env, env.run("implement", json_reply(IMPLEMENT)))
    assert len(env.llm.calls) == calls
    assert len(env.store.records("implement")) == records
    assert env.sandbox.changed == changed


def test_plan_is_refused_while_a_human_is_required(env):
    human_required_env(env)
    calls = len(env.llm.calls)
    assert_refused_for_human(env, env.run("plan", json_reply(PLAN)))
    assert len(env.llm.calls) == calls


def test_escalate_and_reviewer_still_run_while_a_human_is_required(env):
    human_required_env(env)
    assert env.run("escalate", json_reply(ESCALATION))["ok"]
    assert env.run("reviewer", review_reply(), target="implement")["ok"]


def test_revision_cap_is_still_reported_first_while_a_human_is_required(env):
    env.reviewed("triage", TRIAGE)
    assert env.run("plan", json_reply(PLAN))["ok"]
    assert env.run("reviewer", review_reply("revise", REVISE), target="plan")["ok"]
    assert env.run("plan", json_reply(PLAN))["ok"]
    assert env.run("reviewer", review_reply("revise", REVISE), target="plan")["ok"]
    assert env.run("plan", json_reply(PLAN))["error_code"] == "REVISION_CAP_REACHED"


def test_plan_and_implement_run_when_no_human_is_required(env):
    env.reviewed("triage", TRIAGE)
    env.reviewed("plan", PLAN)
    env.sandbox.changed = [PLAN_FILE]
    assert env.run("implement", json_reply(IMPLEMENT))["ok"]
