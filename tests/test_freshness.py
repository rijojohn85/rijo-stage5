"""A pass obtained by re-reviewing unchanged work after a challenge does not count."""
from conftest import IMPLEMENT, PLAN, PLAN_FILE, TRIAGE, json_reply, review_reply
from harness.freshness import Freshness
from harness.run_state import RunState

REVISE = [{"category": "incorrect", "detail": "wrong file"}]
OTHER_PLAN = {**PLAN, "steps": ["fix add, then add a test"]}


def freshness_of(env):
    return Freshness(env.store, env.sandbox)


def state_of(env):
    return RunState(env.store, env.audit, freshness_of(env))


def plan_challenged_then_passed(env, second_plan=None):
    """Plan reviewed `revise`, optionally replaced by `second_plan`, then reviewed `pass`."""
    env.reviewed("triage", TRIAGE)
    assert env.run("plan", json_reply(PLAN))["ok"]
    assert env.run("reviewer", review_reply("revise", REVISE), target="plan")["ok"]
    if second_plan is not None:
        assert env.run("plan", json_reply(second_plan), feedback="fix it")["ok"]
    assert env.run("reviewer", review_reply(), target="plan")["ok"]


def test_a_first_time_pass_counts(env):
    env.reviewed("triage", TRIAGE)
    env.reviewed("plan", PLAN)
    assert freshness_of(env).has_fresh_passing_review("plan")


def test_a_pass_after_a_revise_of_the_same_plan_does_not_count(env):
    plan_challenged_then_passed(env)
    assert not freshness_of(env).has_fresh_passing_review("plan")
    refused = env.run("implement", json_reply(IMPLEMENT))
    assert not refused["ok"] and refused["error_code"] == "PRECONDITION_MISSING"


def test_review_status_shows_the_challenge_not_the_later_pass(env):
    plan_challenged_then_passed(env)
    assert state_of(env).review_status("plan") == "revise"


def test_a_replacement_plan_with_new_content_starts_clean(env):
    plan_challenged_then_passed(env, second_plan=OTHER_PLAN)
    assert freshness_of(env).has_fresh_passing_review("plan")
    env.sandbox.changed = [PLAN_FILE]
    assert env.run("implement", json_reply(IMPLEMENT))["ok"]


def test_an_identical_replacement_spends_the_allowance_and_requires_a_human(env):
    plan_challenged_then_passed(env, second_plan=PLAN)
    assert not freshness_of(env).has_fresh_passing_review("plan")
    assert "plan revision allowance exhausted" in state_of(env).human_reasons()


def test_an_identical_implement_artifact_with_a_different_diff_is_not_challenged(env):
    env.reviewed("triage", TRIAGE)
    env.reviewed("plan", PLAN)
    env.sandbox.changed = [PLAN_FILE]
    assert env.run("implement", json_reply(IMPLEMENT))["ok"]
    assert env.run("reviewer", review_reply("revise", REVISE), target="implement")["ok"]
    env.sandbox.diff_text = "diff --git a/y b/y\n"
    assert env.run("implement", json_reply(IMPLEMENT), feedback="redo")["ok"]
    assert env.run("reviewer", review_reply(), target="implement")["ok"]
    assert freshness_of(env).has_fresh_passing_review("implement")
