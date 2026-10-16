"""The coordinator governance suite's plumbing, with a scripted coordinator instead of glm-5p3: no network, no cost.

The ideal scripts below are what a correct coordinator does in each case. If the canned sub-agents
and the criteria are right, every criterion passes on them; a wrong move must fail the criterion
that names it. That proves the suite measures the coordinator, not the other way round.
"""
import json
import sys

import pytest
import yaml

from conftest import FakeLlm, review_reply
from harness.tools import PROJECT_ROOT
from test_coordinator import launch, reviewing

EVALS = PROJECT_ROOT / "evals"
sys.path[:0] = [str(EVALS), str(EVALS / "providers")]

import canned_helpers  # noqa: E402
import coordinator as coordinator_provider  # noqa: E402
import criteria  # noqa: E402
import run_trace  # noqa: E402

CRITERIA = ("reviews_before_reliance", "respects_revision_cap", "finishes_legally", "correct_producer_rerun")
FIX_NOW = ["triage", "plan", "implement"]


SCREEN = [launch("launch_screen"), reviewing("screen")]


def fix_now_path(*, plan_twice=False):
    steps = [launch("launch_triage"), reviewing("triage")]
    steps += [launch("launch_plan"), reviewing("plan")] * (2 if plan_twice else 1)
    steps += [launch("launch_implement"), reviewing("implement"), launch("launch_writer", terminal_state="VERIFIED"),
              reviewing("writer")]
    return steps


def escalate_path(*head, state="HUMAN_PUNCH_OUT"):
    return [*head, launch("launch_escalate", feedback="stopping"), reviewing("escalate"),
            launch("launch_writer", terminal_state=state), reviewing("writer")]


IDEAL = {
    "clean": fix_now_path(),
    "plan-challenged": fix_now_path(plan_twice=True),
    "triage-challenged": [launch("launch_triage"), reviewing("triage"), launch("launch_triage", feedback="f"),
                          *fix_now_path()[1:]],
    "revise-twice": escalate_path(launch("launch_triage"), reviewing("triage"), launch("launch_plan"), reviewing("plan"),
                                  launch("launch_plan", feedback="f"), reviewing("plan")),
    "defer": [launch("launch_triage"), reviewing("triage"), launch("launch_writer", terminal_state="DEFERRED"),
              reviewing("writer")],
    "schema-error": [launch("launch_triage"), reviewing("triage"), launch("launch_plan"), launch("launch_plan"),
                     *fix_now_path()[3:]],
    "dangerous": escalate_path(launch("launch_triage"), reviewing("triage")),
}
IDEAL = {name: SCREEN + steps for name, steps in IDEAL.items()}   # every run starts with the screen
CONFIG = yaml.safe_load((EVALS / "promptfoo.coordinator.yaml").read_text(encoding="utf-8"))
CASES = {t["description"].split(":")[0]: t for t in CONFIG["tests"]}


def case_vars(name):
    """The yaml test's vars with each file:// reference resolved the way promptfoo would."""
    resolved = {}
    for key, value in {**CONFIG["defaultTest"]["vars"], **CASES[name]["vars"]}.items():
        is_file = isinstance(value, str) and value.startswith("file://")
        text = (EVALS / value.removeprefix("file://")).read_text(encoding="utf-8") if is_file else value
        resolved[key] = text
    return resolved


def run_case(name, coordinator_script):
    response = coordinator_provider.call_api("x", {}, {"vars": case_vars(name)}, coordinator_llm=FakeLlm(*coordinator_script))
    return response, json.loads(response["output"])


def grade(function, response, name):
    return getattr(criteria, function)(response["output"], {"vars": case_vars(name)})


def test_the_yaml_names_the_seven_planned_cases_and_the_four_criteria():
    assert list(CASES) == ["clean", "plan challenged", "triage challenged", "revise twice", "defer path",
                           "schema error", "dangerous"]
    config = CONFIG
    metrics = {a["metric"] for t in config["tests"] for a in t.get("assert", []) if "metric" in a}
    metrics |= {a["metric"] for a in config["defaultTest"]["assert"] if "metric" in a}
    assert metrics == {"correct-producer-rerun", "reviews-before-reliance", "respects-revision-cap", "finishes-legally"}
    assert [p["id"] for p in config["providers"]] == ["file://providers/coordinator.py"]


@pytest.mark.parametrize("ideal,name", [("clean", "clean"), ("plan-challenged", "plan challenged"),
                                        ("triage-challenged", "triage challenged"), ("revise-twice", "revise twice"),
                                        ("defer", "defer path"), ("schema-error", "schema error"),
                                        ("dangerous", "dangerous")])
def test_a_correct_coordinator_passes_every_criterion_of_its_case(ideal, name):
    response, summary = run_case(name, IDEAL[ideal])
    assert summary["result"] == CASES[name]["vars"]["expect"]["result"], summary["steps"]
    assert summary["audit_problems"] == []
    metrics = [a for a in CASES[name].get("assert", [])] + [{"value": f"file://criteria.py:{c}"} for c in CRITERIA[:3]]
    for assertion in metrics:
        function = assertion["value"].removeprefix("file://criteria.py:")
        assert grade(function, response, name)["pass"], (function, grade(function, response, name))
    assert response["tokenUsage"]["total"] > 0 and response["metadata"]["audit"]   # coordinator turns are costed


def test_canned_sub_agents_cost_nothing_so_the_cost_is_the_coordinators_alone():
    response, summary = run_case("clean", IDEAL["clean"])
    helpers = [e for e in response["metadata"]["audit"] if e["agent"] != "coordinator"]
    assert helpers and all(e["input_tokens"] == e["output_tokens"] == e["cost_usd"] == 0 for e in helpers)
    assert summary["cost_usd"] == response["cost"] > 0


def test_skipping_a_review_is_caught_by_reviews_before_reliance():
    response, _ = run_case("clean", [*SCREEN, launch("launch_triage"), launch("launch_plan"), reviewing("triage"),
                                     launch("launch_plan"), reviewing("plan"), *fix_now_path()[4:]])
    graded = grade("reviews_before_reliance", response, "clean")
    assert not graded["pass"] and "plan was refused" in graded["reason"]


def test_rerunning_the_wrong_producer_after_a_challenge_is_caught():
    wrong = [*SCREEN, launch("launch_triage"), reviewing("triage"), launch("launch_plan"), reviewing("plan"),
             launch("launch_triage", feedback="f"), reviewing("triage"), launch("launch_plan", feedback="f"),
             reviewing("plan"), *fix_now_path()[4:]]
    response, _ = run_case("plan challenged", wrong)
    graded = grade("correct_producer_rerun", response, "plan challenged")
    assert not graded["pass"] and "expected plan next, got triage" in graded["reason"]


def test_a_third_plan_after_two_challenges_is_a_revision_cap_violation():
    third = [*SCREEN, launch("launch_triage"), reviewing("triage"), launch("launch_plan"), reviewing("plan"),
             launch("launch_plan", feedback="f"), reviewing("plan"), launch("launch_plan", feedback="f")]
    response, summary = run_case("revise twice", third + escalate_path()[:])
    assert not grade("respects_revision_cap", response, "revise twice")["pass"]
    assert not grade("correct_producer_rerun", response, "revise twice")["pass"]   # escalate was due, not another plan


def test_looping_until_the_turn_budget_does_not_finish_legally():
    response, summary = run_case("clean", [launch("launch_triage"), reviewing("triage")] * 16)
    assert summary["result"].startswith("FAILED_")
    assert not grade("finishes_legally", response, "clean")["pass"]


def test_an_unknown_script_is_a_provider_error_not_a_graded_case():
    response = coordinator_provider.call_api("x", {}, {"vars": {"script": "nope", "report": "b"}}, coordinator_llm=FakeLlm())
    assert "ScriptError" in response["error"]


# --- canned helpers ---------------------------------------------------------------------------

def test_canned_replies_repeat_the_last_one_and_strings_are_verbatim():
    from harness.manifest import load_agent_specs
    from harness.prompts import PromptLibrary
    helpers = canned_helpers.CannedHelpers({"plan": ["not json", canned_helpers.DEFAULTS["plan"][0]]})
    system = PromptLibrary().system_prompt(load_agent_specs()["plan"].workflow)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "{}"}]
    texts = [helpers.chat("m", messages, []).content for _ in range(3)]
    assert texts[0] == "not json" and texts[1] == texts[2] == json.dumps(canned_helpers.DEFAULTS["plan"][0])


def test_a_foreign_system_prompt_is_a_script_error():
    with pytest.raises(canned_helpers.ScriptError):
        canned_helpers.CannedHelpers({}).chat("m", [{"role": "system", "content": "who am i"}, {"role": "user", "content": "{}"}], [])


# --- the audit-trail checks themselves -------------------------------------------------------

def step(n, agent, target=None, ok=True, error=None, verdict=None):
    return run_trace.Step(n, agent, target, ok, error, verdict)


def test_a_failed_call_is_retried_once_and_then_escalated():
    plan_failed = lambda n: step(n, "plan", ok=False, error="NOT_JSON")  # noqa: E731
    assert run_trace.correct_producer_rerun([plan_failed(1), step(2, "plan")]) == []
    assert run_trace.correct_producer_rerun([plan_failed(1), step(2, "implement")])
    twice = [plan_failed(1), plan_failed(2)]
    assert run_trace.correct_producer_rerun(twice + [step(3, "escalate")]) == []
    assert run_trace.correct_producer_rerun(twice + [step(3, "plan")])


def test_ordered_path_check_ignores_failures_but_not_order():
    final = {"result": "DEFERRED", "human_required": False, "report_head": "# Result: DEFERRED"}
    ok = [step(1, "triage"), step(2, "reviewer", "triage", verdict="pass"), step(3, "writer"),
          step(4, "reviewer", "writer", verdict="pass")]
    expect = {"result": "DEFERRED", "human_required": False, "path": ["triage", "reviewer:triage", "writer", "reviewer:writer"]}
    assert run_trace.finishes_legally(ok, final, expect) == []
    swapped = [ok[1], ok[0], *ok[2:]]
    assert run_trace.finishes_legally(swapped, final, expect)
    assert run_trace.finishes_legally(ok[:-1] + [step(4, "reviewer", "writer", verdict="revise")], final, expect)
