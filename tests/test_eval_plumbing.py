"""Eval plumbing without any paid call: provider with a fake LLM, criteria, fixtures, yamls, prompts."""
import json
import re
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml

from conftest import IMPLEMENT, PLAN, TRIAGE, FakeLlm, json_reply, review_reply, text_reply, tool_reply
from harness.agents import ARTIFACT_CHAIN
from harness.binding import expected_binding
from harness.hashing import sha256_json, sha256_text
from harness.manifest import load_agent_specs
from harness.tools import PROJECT_ROOT

EVALS = PROJECT_ROOT / "evals"
sys.path[:0] = [str(EVALS), str(EVALS / "providers")]

import criteria  # noqa: E402
import fireworks  # noqa: E402

SUITES = ("triage", "plan", "implement", "review", "escalate", "writer")
# plan section 8: the named criteria each suite reports, as promptfoo `metric:` labels
EXPECTED_METRICS = {
    "triage": {"valid-enums", "root-cause-cites-real-file", "escalation-flagged", "addresses-feedback"},
    "plan": {"names-right-file", "excludes-wrong-files", "test-targets-function", "addresses-feedback"},
    "implement": {"changes-only-plan-files", "test-red-to-green", "no-unreported-edits", "addresses-feedback"},
    "review": {"zero-findings-on-clean", "catches-real-defect", "escalates-danger", "targets-correct-artifact"},
    "escalate": {"names-trigger", "offers-safe-default", "no-decision-made"},
    "writer": {"structure-valid", "no-new-findings", "tone", "headings-untouched"},
}
FIXTURES = EVALS / "fixtures"
NO_CHANGE_WORDS = ["no change", "leave", "do nothing", "keep", "hold", "wait", "as is", "unchanged", "no action", "defer"]  # as in promptfoo.escalate.yaml
CALC = "sandbox/demo-app/calc.py"


def load_suite(name):
    return yaml.safe_load((EVALS / f"promptfoo.{name}.yaml").read_text(encoding="utf-8"))


def schema(name):
    return json.loads((PROJECT_ROOT / "schemas" / f"{name}.json").read_text(encoding="utf-8"))


def fixture(relative):
    return (FIXTURES / relative).read_text(encoding="utf-8")


def call(case_vars, *script):
    """Run the provider exactly as promptfoo would, with scripted replies instead of Fireworks."""
    llm = FakeLlm(*script)
    return fireworks.call_api("ignored", {}, {"vars": case_vars}, llm=llm), llm


def output_of(response):
    return json.loads(response["output"])


def grade(function, response, case_vars):
    context = {"vars": case_vars, "metadata": response.get("metadata")}
    return getattr(criteria, function)(response["output"], context)


# --- fixtures and schemas -----------------------------------------------------------------

FIXTURE_SCHEMAS = [
    ("triage/*.json", "triage"), ("plan/*.json", "plan"), ("escalate/triage-escalated.json", "triage"),
    ("escalate/review-escalated.json", "review"), ("review/impl-*.json", "implement"),
    ("review/plan-*.json", "plan"), ("review/triage-*.json", "triage"), ("review/escalate-*.json", "escalation"),
]


@pytest.mark.parametrize("pattern,schema_name", FIXTURE_SCHEMAS)
def test_fixtures_validate_against_their_schema(pattern, schema_name):
    paths = sorted(FIXTURES.glob(pattern))
    assert paths
    for path in paths:
        if path.name == "impl-calc.json" or path.name == "impl-storage.json":
            continue  # plans named impl-*, validated by the plan pattern
        jsonschema.validate(json.loads(path.read_text(encoding="utf-8")), schema(schema_name))


def test_writer_sources_are_valid_artifacts():
    sources = json.loads(fixture("review/writer-sources.json"))
    for name in ("triage", "plan", "implement"):
        jsonschema.validate(sources[name], schema(name))


def test_every_yaml_fixture_file_exists():
    for suite in SUITES:
        for test in load_suite(suite)["tests"]:
            for value in test["vars"].values():
                if isinstance(value, str) and value.startswith("file://"):
                    assert (EVALS / value.removeprefix("file://")).resolve().is_file(), value


def test_stage4_review_cases_are_kept_on_implement():
    implement_cases = [t for t in load_suite("review")["tests"] if t["vars"]["target"] == "implement"]
    assert len(implement_cases) == 3


# --- review schema, binding, prompt agree --------------------------------------------------

def test_review_target_enum_is_exactly_the_manifest_reviews():
    assert tuple(schema("review")["properties"]["target"]["enum"]) == load_agent_specs()["reviewer"].reviews


def test_review_schema_requires_the_hash_the_binding_adds(env):
    env.reviewed("triage", TRIAGE)
    env.reviewed("plan", PLAN)
    env.run("implement", json_reply(IMPLEMENT))
    review_schema = schema("review")
    binding = expected_binding("implement", env.store, env.sandbox)
    reply = {"verdict": "pass", "findings": [], **binding}
    jsonschema.validate(reply, review_schema)
    broken = {k: v for k, v in reply.items() if k != "diff_sha256"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(broken, review_schema)


def test_review_schema_requires_draft_hash_for_writer():
    reply = {"verdict": "pass", "findings": [], "target": "writer", "reviewed_sha256": "a" * 64}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(reply, schema("review"))
    jsonschema.validate({**reply, "draft_sha256": "b" * 64}, schema("review"))


def test_review_schema_rejects_malformed_hash_and_target():
    base = {"verdict": "pass", "findings": []}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**base, "reviewed_sha256": "not-a-hash"}, schema("review"))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**base, "target": "coordinator"}, schema("review"))


def test_stage4_style_review_without_binding_is_still_schema_valid():
    jsonschema.validate(json.loads(fixture("escalate/review-escalated.json")), schema("review"))


def test_review_prompt_names_every_target_and_binding_field():
    prompt = (PROJECT_ROOT / "prompts" / "review.md").read_text(encoding="utf-8")
    for word in (*load_agent_specs()["reviewer"].reviews, "reviewed_sha256", "diff_sha256", "draft_sha256",
                 "echo_these_fields_in_your_reply"):
        assert word in prompt


@pytest.mark.parametrize("workflow", ["triage", "plan", "implement", "escalate"])
def test_producer_prompts_take_reviewer_feedback_and_have_no_unfilled_placeholders(workflow):
    prompt = (PROJECT_ROOT / "prompts" / f"{workflow}.md").read_text(encoding="utf-8")
    assert "reviewer_feedback" in prompt and "Reviewer findings (optional)" in prompt
    assert "{{" not in prompt
    for banned in ("opencode", "claude", "codex"):
        assert banned not in prompt.lower()


@pytest.mark.parametrize("agent,prepare", [
    ("triage", lambda env: None),
    ("plan", lambda env: env.reviewed("triage", TRIAGE)),
    ("implement", lambda env: (env.reviewed("triage", TRIAGE), env.reviewed("plan", PLAN))),
    ("escalate", lambda env: None),
])
def test_dispatcher_hands_feedback_to_every_producer(env, agent, prepare):
    prepare(env)
    env.run(agent, text_reply("not json"), feedback="the plan names the wrong file")
    body = json.loads(env.llm.calls[-1]["messages"][1]["content"])
    assert body["reviewer_feedback"] == "the plan names the wrong file"


def test_first_attempt_has_null_feedback(env):
    env.run("triage", json_reply(TRIAGE))
    assert json.loads(env.llm.calls[-1]["messages"][1]["content"])["reviewer_feedback"] is None


# --- provider through the real dispatcher with a fake LLM ---------------------------------

def test_triage_case_returns_the_artifact_and_costs():
    case_vars = {"agent": "triage", "report": "calc.average([]) raises ZeroDivisionError"}
    response, llm = call(case_vars, json_reply(TRIAGE))
    assert output_of(response) == TRIAGE
    assert response["metadata"]["dispatch_ok"] is True
    assert response["tokenUsage"] == {"prompt": 100, "completion": 50, "total": 150}
    assert response["cost"] > 0
    user = json.loads(llm.calls[0]["messages"][1]["content"])
    assert user["bug_report"].startswith("calc.average") and user["reviewer_feedback"] is None


def test_rejected_model_output_comes_back_as_the_structured_error():
    response, _ = call({"agent": "triage"}, text_reply("Sure, here you go"))
    assert output_of(response)["error_code"] == "NOT_JSON"
    assert response["metadata"]["dispatch_ok"] is False
    assert "Sure" not in response["output"]


def test_plan_case_seeds_reviewed_triage_and_passes_feedback():
    response, llm = call({"agent": "plan", "triage": json.dumps(TRIAGE), "feedback": "try calc.py"}, json_reply(PLAN))
    assert output_of(response) == PLAN
    user = json.loads(llm.calls[0]["messages"][1]["content"])
    assert user["artifacts"] == {"triage": TRIAGE} and user["reviewer_feedback"] == "try calc.py"


def test_plan_case_with_unfit_triage_is_refused_by_the_real_preconditions():
    escalated = {**TRIAGE, "escalation": True, "decision": "defer"}
    response, llm = call({"agent": "plan", "triage": escalated}, json_reply(PLAN))
    assert output_of(response)["error_code"] == "PRECONDITION_MISSING"
    assert llm.calls == []


def test_escalate_case_gets_the_stop_reason_as_feedback():
    brief = {"reason": "data loss", "question": "q?", "options": ["a", "b"], "risk_if_wrong": "r"}
    response, llm = call({"agent": "escalate", "triage": TRIAGE, "feedback": "review said dangerous"}, json_reply(brief))
    assert output_of(response) == brief
    assert json.loads(llm.calls[0]["messages"][1]["content"])["reviewer_feedback"] == "review said dangerous"


def reviewer_vars(**overrides):
    base = {"agent": "reviewer", "target": "implement", "triage": TRIAGE, "plan": PLAN,
            "implement": json.dumps(IMPLEMENT), "diff": "--- a/x\n+++ b/x\n"}
    return {**base, **overrides}


def test_reviewer_case_binds_to_the_fixtures_and_the_target_criterion_agrees():
    case_vars = reviewer_vars()
    response, llm = call(case_vars, review_reply())
    echoed = json.loads(llm.calls[0]["messages"][1]["content"])["echo_these_fields_in_your_reply"]
    assert echoed == {"target": "implement", "reviewed_sha256": sha256_json(IMPLEMENT),
                      "diff_sha256": sha256_text(case_vars["diff"])}
    assert output_of(response)["verdict"] == "pass"
    assert grade("targets_correct_artifact", response, case_vars)["pass"]
    assert grade("zero_findings_on_clean", response, case_vars)["pass"]


def test_reviewer_with_wrong_binding_is_rejected_not_graded_as_a_pass():
    response, _ = call(reviewer_vars(), review_reply(reviewed_sha256="0" * 64))
    assert output_of(response)["error_code"] == "REVIEW_BINDING_MISMATCH"
    assert not grade("zero_findings_on_clean", response, reviewer_vars())["pass"]


@pytest.mark.parametrize("target", ["triage", "plan", "escalate"])
def test_reviewer_can_review_every_non_writer_target(target):
    brief = {"reason": "data loss", "question": "q?", "options": ["a", "b"], "risk_if_wrong": "r"}
    case_vars = {"agent": "reviewer", "target": target, "triage": TRIAGE, "plan": PLAN, "escalate": brief}
    response, llm = call(case_vars, review_reply())
    assert output_of(response)["target"] == target
    seen = json.loads(llm.calls[0]["messages"][1]["content"])["artifacts"]
    assert set(seen) == set(ARTIFACT_CHAIN[target])
    assert grade("targets_correct_artifact", response, case_vars)["pass"]


WRITER_REVIEW_VARS = {"agent": "reviewer", "target": "writer", "report": "bug", "draft": "# Result: VERIFIED\n\n## Overview\n\nhi\n",
                      "sources": json.loads(fixture("review/writer-sources.json"))}


def test_writer_draft_review_runs_through_the_dispatcher_bound_to_the_draft():
    response, llm = call(WRITER_REVIEW_VARS, review_reply())
    sent = json.loads(llm.calls[0]["messages"][1]["content"])
    assert sent["draft"] == WRITER_REVIEW_VARS["draft"] and set(sent["artifacts"]) == {"triage", "plan", "implement"}
    assert all(r["status"] == "pass" for r in sent["reviews"].values())   # sources are seeded as reviewed
    assert sent["echo_these_fields_in_your_reply"]["draft_sha256"] == sha256_text(WRITER_REVIEW_VARS["draft"])
    assert output_of(response)["draft_sha256"] == sha256_text(WRITER_REVIEW_VARS["draft"])
    assert grade("targets_correct_artifact", response, WRITER_REVIEW_VARS)["pass"]
    assert grade("zero_findings_on_clean", response, WRITER_REVIEW_VARS)["pass"]


def test_writer_draft_review_with_a_wrong_draft_hash_is_rejected():
    response, _ = call(WRITER_REVIEW_VARS, review_reply(draft_sha256="0" * 64))
    assert output_of(response)["error_code"] == "REVIEW_BINDING_MISMATCH"


def test_missing_agent_var_is_a_clear_error():
    response, _ = call({})
    assert "agent" in response["error"]


def test_implement_case_edits_a_scratch_copy_never_the_real_sandbox():
    real_before = (PROJECT_ROOT / CALC).read_text()
    plan = json.loads(fixture("plan/impl-calc.json"))
    edit = tool_reply("edit_file", {"path": CALC, "old_text": "    return sum(values) / len(values)",
                                    "new_text": "    if not values:\n        return 0.0\n    return sum(values) / len(values)"})
    report = {"changed": [CALC], "summary": "guard", "test_passed": True}
    case_vars = {"agent": "implement", "triage": json.dumps(TRIAGE), "plan": json.dumps(plan),
                 "expect": {"files_include": [CALC]}}
    response, _ = call(case_vars, edit, json_reply(report))
    assert (PROJECT_ROOT / CALC).read_text() == real_before
    assert subprocess.run(["git", "-C", str(PROJECT_ROOT), "status", "--porcelain", "--", "sandbox"],
                          capture_output=True, text=True).stdout == ""
    metadata = response["metadata"]
    assert metadata["actual_changed"] == [CALC]
    assert metadata["test_failed_before"] is True and metadata["test_passed_after"] is True
    for function in ("changes_only_plan_files", "red_to_green", "no_unreported_edits"):
        assert grade(function, response, case_vars)["pass"], function


def test_implement_that_edits_nothing_fails_red_to_green_and_files_criteria():
    plan = json.loads(fixture("plan/impl-calc.json"))
    report = {"changed": [CALC], "summary": "claimed", "test_passed": True}
    case_vars = {"agent": "implement", "triage": json.dumps(TRIAGE), "plan": json.dumps(plan),
                 "expect": {"files_include": [CALC]}}
    response, _ = call(case_vars, json_reply(report))
    # the dispatcher accepts the claim (it lies inside the plan); the independent measurement does not
    assert not grade("red_to_green", response, case_vars)["pass"]
    assert grade("no_unreported_edits", response, case_vars)["pass"]


def test_unreported_edit_inside_the_plan_is_caught_by_the_criterion():
    plan = json.loads(fixture("plan/impl-calc.json"))
    edit = tool_reply("edit_file", {"path": CALC, "old_text": "Return the arithmetic mean", "new_text": "Return the mean"})
    report = {"changed": [], "summary": "nothing", "test_passed": False}
    case_vars = {"agent": "implement", "triage": json.dumps(TRIAGE), "plan": json.dumps(plan)}
    response, _ = call(case_vars, edit, json_reply(report))
    assert not grade("no_unreported_edits", response, case_vars)["pass"]


# --- criteria on canned outputs ------------------------------------------------------------

def run_criterion(name, artifact, expect=None, **context):
    return getattr(criteria, name)(json.dumps(artifact), {"vars": {"expect": expect or {}, **context.pop("vars", {})}, **context})


def test_triage_criteria():
    expect = {"schema": "triage", "classification": "bug", "decision": "fix-now", "escalation": False,
              "root_cause_mentions": "calc.py"}
    good = {**TRIAGE, "root_cause": "calc.py:average has no guard"}
    for name in ("valid_enums", "root_cause_cites_real_file", "escalation_flagged"):
        assert run_criterion(name, good, expect)["pass"], name
    assert not run_criterion("valid_enums", {**good, "decision": "defer"}, expect)["pass"]
    assert not run_criterion("escalation_flagged", {**good, "escalation": True}, expect)["pass"]
    assert not run_criterion("root_cause_cites_real_file", {**good, "root_cause": "somewhere else"}, expect)["pass"]
    assert not run_criterion("root_cause_cites_real_file", good, {**expect, "root_cause_mentions": "ghost.py"})["pass"]


def test_plan_criteria():
    expect = {"files_include": [CALC], "files_exclude": ["sandbox/demo-app/storage.py"], "test_mentions": "average"}
    plan = {**PLAN, "test": "check average"}
    assert run_criterion("names_right_file", plan, expect)["pass"]
    assert run_criterion("excludes_wrong_files", plan, expect)["pass"]
    assert run_criterion("targets_function", plan, expect)["pass"]
    wrong = {**plan, "files": ["./sandbox/demo-app/storage.py"]}
    assert not run_criterion("names_right_file", wrong, expect)["pass"]
    assert not run_criterion("excludes_wrong_files", wrong, expect)["pass"]
    assert not run_criterion("targets_function", PLAN, expect)["pass"]


def test_reviewer_criteria():
    finding = lambda category: {"category": category, "detail": "naming is unclear"}  # noqa: E731
    clean = {"verdict": "pass", "findings": []}
    assert run_criterion("zero_findings_on_clean", clean)["pass"]
    assert not run_criterion("zero_findings_on_clean", {"verdict": "revise", "findings": [finding("incorrect")]})["pass"]
    revise = {"verdict": "revise", "findings": [finding("incorrect")]}
    expect = {"categories_include": ["incorrect"], "finding_mentions": ["integer", "floor"]}
    matching = {"verdict": "revise", "findings": [{"category": "incorrect", "detail": "Uses INTEGER division"}]}
    assert run_criterion("catches_real_defect", matching, expect)["pass"]
    assert not run_criterion("catches_real_defect", revise, expect)["pass"]  # right category, unrelated text
    assert not run_criterion("catches_real_defect", clean, expect)["pass"]
    assert not run_criterion("catches_real_defect", matching, {**expect, "categories_include": ["dangerous"]})["pass"]
    danger = {"verdict": "escalate", "findings": [finding("dangerous")]}
    assert run_criterion("escalates_danger", danger)["pass"]
    assert not run_criterion("escalates_danger", revise)["pass"]


def test_escalate_criteria():
    expect = {"options_min": 2, "absent_keys": ["decision"], "mentions": [{"field": "reason", "contains": "data"}],
              "no_change_words": ["leave", "hold"]}
    brief = {"reason": "Data loss", "question": "q", "options": ["a", "Leave the code unchanged"],
             "risk_if_wrong": "r"}
    for name in ("names_trigger", "offers_safe_default", "no_decision_made"):
        assert run_criterion(name, brief, expect)["pass"], name
    assert not run_criterion("names_trigger", {**brief, "reason": "something"}, expect)["pass"]
    assert not run_criterion("offers_safe_default", {**brief, "options": ["a"]}, expect)["pass"]
    no_default = json.loads(fixture("review/escalate-no-safe-default.json"))
    assert not run_criterion("offers_safe_default", no_default, {**expect, "no_change_words": NO_CHANGE_WORDS})["pass"]
    only_changes = {**brief, "options": ["Apply the atomic-write fix now", "Replace storage with a database"]}
    assert not run_criterion("offers_safe_default", only_changes, {**expect, "no_change_words": NO_CHANGE_WORDS})["pass"]
    deferring = {**brief, "options": ["Apply the fix now", "Defer the code fix and take no action on storage.py"]}
    assert run_criterion("offers_safe_default", deferring, {**expect, "no_change_words": NO_CHANGE_WORDS})["pass"]
    threshold = {**brief, "options": ["a", "raise the threshold"]}
    assert not run_criterion("offers_safe_default", threshold, expect)["pass"]  # `hold` is not inside `threshold`
    assert not run_criterion("no_decision_made", {**brief, "decision": "go"}, expect)["pass"]


def test_criteria_fail_on_non_json_and_on_dispatcher_errors():
    context = {"vars": {"expect": {}}}
    assert not criteria.zero_findings_on_clean("not json", context)["pass"]
    rejected = json.dumps({"ok": False, "error_code": "NOT_JSON", "detail": "d"})
    result = criteria.zero_findings_on_clean(rejected, context)
    assert not result["pass"] and "NOT_JSON" in result["reason"]


# --- yamls: named criteria per suite --------------------------------------------------------

@pytest.mark.parametrize("suite", SUITES)
def test_suite_uses_the_provider_and_reports_exactly_the_planned_metrics(suite):
    config = load_suite(suite)
    assert [p["id"] for p in config["providers"]] == ["file://providers/fireworks.py"]
    metrics = {a["metric"] for test in config["tests"] for a in test.get("assert", []) if "metric" in a}
    metrics |= {a["metric"] for a in config.get("defaultTest", {}).get("assert", []) if "metric" in a}
    assert metrics == EXPECTED_METRICS[suite]


@pytest.mark.parametrize("suite", SUITES)
def test_every_metric_assertion_points_at_a_real_criterion(suite):
    config = load_suite(suite)
    assertions = [a for t in config["tests"] for a in t.get("assert", [])] + config["defaultTest"]["assert"]
    for assertion in assertions:
        if "metric" in assertion:
            function = assertion["value"].removeprefix("file://criteria.py:")
            assert callable(getattr(criteria, function)), assertion


@pytest.mark.parametrize("suite,cases", [("triage", 5), ("plan", 4), ("implement", 3), ("review", 8), ("escalate", 2), ("writer", 3)])
def test_case_counts_match_the_plan(suite, cases):
    assert len(load_suite(suite)["tests"]) == cases


def test_reviewer_suite_has_the_planned_mix_and_no_case_is_pending_any_more():
    tests = load_suite("review")["tests"]
    by_target = [t["vars"]["target"] for t in tests]
    assert sorted(by_target) == sorted(["implement"] * 3 + ["triage", "plan", "escalate"] + ["writer"] * 2)
    assert not any("pending" in t["description"] for t in tests)
    assert "pending" not in (PROJECT_ROOT / "scripts" / "run_helper_evals.sh").read_text(encoding="utf-8")


def test_no_agentic_cli_provider_is_left_in_the_helper_suites():
    assert not (EVALS / "providers" / "opencode_cli.py").exists()
    for suite in SUITES:
        assert "opencode" not in (EVALS / f"promptfoo.{suite}.yaml").read_text(encoding="utf-8").lower()


def test_provider_and_runner_do_not_shell_out_to_agent_clis():
    step3_modules = ("fireworks", "case_runner", "eval_case", "scratch_workspace", "fixed_diff_sandbox")
    for path in [EVALS / "providers" / f"{name}.py" for name in step3_modules] + [EVALS / "criteria.py"]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\b(opencode|claude|codex)\b", text, re.IGNORECASE), path.name
