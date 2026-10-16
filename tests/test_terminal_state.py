"""One shared review rule for every terminal state: each producer's latest fresh artifact needs a fresh bound review."""
from conftest import ESCALATION, IMPLEMENT, PLAN, PLAN_FILE, TRIAGE, json_reply, review_reply, text_reply
from harness.run_views import RunViews

DEFER = {**TRIAGE, "decision": "defer"}
ESCALATED_TRIAGE = {**TRIAGE, "decision": "defer", "escalation": True}
DANGEROUS = [{"category": "dangerous", "detail": "the fix deletes customer records"}]
REVISE = [{"category": "incorrect", "detail": "wrong file"}]


def check(env, state):
    return RunViews.build(env.run_dir, env.sandbox).terminal_check.run(state)


def test_deferred_is_refused_with_an_unreviewed_escalate_brief(env):
    env.reviewed("triage", DEFER)
    assert env.run("escalate", json_reply(ESCALATION))["ok"]
    failure = check(env, "DEFERRED")
    assert failure is not None and "escalate" in failure.detail


def test_deferred_is_accepted_when_every_artifact_passed_review(env):
    env.reviewed("triage", DEFER)
    assert check(env, "DEFERRED") is None


def test_punch_out_is_refused_with_an_unreviewed_plan_until_it_is_reviewed(env):
    env.reviewed("triage", TRIAGE)
    assert env.run("plan", json_reply(PLAN))["ok"]
    assert not env.run("plan", text_reply("nope"))["ok"]
    assert not env.run("plan", text_reply("nope"))["ok"]  # two failures in a row require a human
    env.reviewed("escalate", ESCALATION)
    failure = check(env, "HUMAN_PUNCH_OUT")
    assert failure is not None and "plan" in failure.detail
    assert env.run("reviewer", review_reply("revise", REVISE), target="plan")["ok"]
    assert check(env, "HUMAN_PUNCH_OUT") is None


def test_punch_out_accepts_an_adverse_review_of_implement(env):
    env.reviewed("triage", TRIAGE)
    env.reviewed("plan", PLAN)
    env.sandbox.changed = [PLAN_FILE]
    assert env.run("implement", json_reply(IMPLEMENT))["ok"]
    assert env.run("reviewer", review_reply("escalate", DANGEROUS), target="implement")["ok"]
    env.reviewed("escalate", ESCALATION)
    assert check(env, "HUMAN_PUNCH_OUT") is None


def test_punch_out_skips_a_stale_unreviewed_plan(env):
    env.reviewed("triage", TRIAGE)
    assert env.run("plan", json_reply(PLAN))["ok"]
    assert env.run("triage", json_reply(ESCALATED_TRIAGE), feedback="rethink")["ok"]  # the old plan is now stale
    assert env.run("reviewer", review_reply(), target="triage")["ok"]
    env.reviewed("escalate", ESCALATION)
    assert check(env, "HUMAN_PUNCH_OUT") is None
