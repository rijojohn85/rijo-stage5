"""Revision eval cases (a producer re-run with reviewer feedback): no paid call, fake LLM only."""
import json

import pytest

from conftest import json_reply, tool_reply
from test_eval_plumbing import CALC, EVALS, call, fixture, grade, load_suite, output_of

import criteria  # noqa: E402  (test_eval_plumbing puts evals/ on sys.path)
from providers.eval_case import EvalCase  # noqa: E402

REVISION_SUITES = ("triage", "plan", "implement")
STORAGE = "sandbox/demo-app/storage.py"
GOOD_REVISIONS = {
    "triage": {"classification": "bug", "decision": "defer", "escalation": True,
               "root_cause": "storage.py:save_records truncates the file before json.dumps runs",
               "rationale": "customer data was lost twice; a human must decide"},
    "plan": {"files": [CALC], "steps": ["guard average()"],
             "test": "python3 -c \"import calc; assert calc.average([]) == 0.0\"  # average"},
    "implement": {"changed": [CALC], "summary": "Added the empty-list guard in calc.py only.", "test_passed": True},
}


def revision_case(suite):
    cases = [t for t in load_suite(suite)["tests"] if t["description"].startswith("[revision]")]
    assert len(cases) == 1
    return cases[0]


def resolved_vars(suite):
    """The test's vars as promptfoo hands them to the provider: file:// values read as text."""
    resolved = {"agent": suite}
    for key, value in revision_case(suite)["vars"].items():
        is_file = isinstance(value, str) and value.startswith("file://")
        resolved[key] = (EVALS / value.removeprefix("file://")).read_text(encoding="utf-8") if is_file else value
    return resolved


def addresses(artifact, suite):
    return criteria.addresses_feedback(json.dumps(artifact), {"vars": resolved_vars(suite), "metadata": {}})


@pytest.mark.parametrize("suite", REVISION_SUITES)
def test_revision_case_loads_and_carries_feedback_and_first_pass(suite):
    case_vars = resolved_vars(suite)
    case = EvalCase.from_vars(case_vars)
    assert case.feedback and case.feedback == case_vars["feedback"]
    assert json.loads(case_vars["first_pass"])  # a JSON artifact
    assert set(case_vars["expect"]["fixes"]) <= {n for n in dir(criteria) if not n.startswith("_")}


@pytest.mark.parametrize("suite", REVISION_SUITES)
def test_addresses_feedback_is_attached_only_to_revision_cases(suite):
    for test in load_suite(suite)["tests"]:
        metrics = {a.get("metric") for a in test["assert"]}
        assert ("addresses-feedback" in metrics) == test["description"].startswith("[revision]")


@pytest.mark.parametrize("suite", REVISION_SUITES)
def test_revision_case_keeps_the_suites_existing_criteria(suite):
    revision = {a["metric"] for a in revision_case(suite)["assert"]}
    plain = next(t for t in load_suite(suite)["tests"] if not t["description"].startswith("[revision]"))
    assert {a["metric"] for a in plain["assert"]} <= revision


def test_first_pass_fixtures_really_have_the_defect():
    triage = json.loads(fixture("review/triage-missed-data-loss.json"))
    plan = json.loads(fixture("review/plan-wrong-file.json"))
    impl = json.loads(fixture("revision/impl-calc-out-of-scope.json"))
    assert triage["escalation"] is False
    assert CALC not in plan["files"] and STORAGE in plan["files"]
    assert STORAGE in impl["changed"] and impl["test_passed"] is False


def test_triage_good_revision_passes_and_ignored_feedback_fails():
    assert addresses(GOOD_REVISIONS["triage"], "triage")["pass"]
    first_pass = json.loads(fixture("review/triage-missed-data-loss.json"))
    assert "identical" in addresses(first_pass, "triage")["reason"]
    changed_but_ignored = {**first_pass, "rationale": "reworded only"}
    assert "escalation" in addresses(changed_but_ignored, "triage")["reason"]


def test_plan_good_revision_passes_and_ignored_feedback_fails():
    assert addresses(GOOD_REVISIONS["plan"], "plan")["pass"]
    first_pass = json.loads(fixture("review/plan-wrong-file.json"))
    assert "identical" in addresses(first_pass, "plan")["reason"]
    reworded = {**first_pass, "steps": ["different words, same wrong file"]}
    assert "files is missing" in addresses(reworded, "plan")["reason"]
    both = {**GOOD_REVISIONS["plan"], "files": [CALC, STORAGE]}
    assert "must not include" in addresses(both, "plan")["reason"]


def test_implement_good_revision_passes_and_ignored_feedback_fails():
    case_vars = resolved_vars("implement")
    edit = tool_reply("edit_file", {"path": CALC, "old_text": "    return sum(values) / len(values)",
                                    "new_text": "    if not values:\n        return 0.0\n    return sum(values) / len(values)"})
    response, _ = call(case_vars, edit, json_reply(GOOD_REVISIONS["implement"]))
    assert grade("addresses_feedback", response, case_vars)["pass"]
    for function in ("changes_only_plan_files", "red_to_green", "no_unreported_edits"):
        assert grade(function, response, case_vars)["pass"], function
    ignored, _ = call(case_vars, json_reply(GOOD_REVISIONS["implement"]))  # claims success, edits nothing
    assert "does not pass" in grade("addresses_feedback", ignored, case_vars)["reason"]
    unchanged = {"agent": "implement", **{k: v for k, v in case_vars.items() if k != "agent"},
                 "first_pass": json.dumps(GOOD_REVISIONS["implement"])}
    same, _ = call(unchanged, edit, json_reply(GOOD_REVISIONS["implement"]))
    assert "identical" in grade("addresses_feedback", same, unchanged)["reason"]


@pytest.mark.parametrize("suite,reply", [
    ("triage", GOOD_REVISIONS["triage"]),
    ("plan", GOOD_REVISIONS["plan"]),
    ("implement", GOOD_REVISIONS["implement"]),
])
def test_provider_hands_reviewer_feedback_to_the_dispatcher(suite, reply):
    case_vars = resolved_vars(suite)
    response, llm = call(case_vars, json_reply(reply))
    assert response["metadata"]["dispatch_ok"] is True
    assert output_of(response) == reply
    body = json.loads(llm.calls[0]["messages"][1]["content"])
    assert body["reviewer_feedback"] == case_vars["feedback"]
    first_pass = json.loads(case_vars["first_pass"])
    assert body["previous_artifact"] == first_pass  # the producer sees the artifact it is revising
    assert criteria.sha256_json(first_pass) != criteria.sha256_json(reply)  # and the criterion still compares to it


def test_criteria_resolve_existing_checks_when_loaded_under_another_module_name():
    """promptfoo loads evals/criteria.py by path under its own module name; lookups must not depend on __name__."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("loaded_by_promptfoo", "evals/criteria.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module._defect_check("escalation_flagged"))
