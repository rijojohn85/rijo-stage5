"""The writer eval suite's plumbing with fake models: seeding, the real dispatcher, criteria and the judge."""
import json
import re
import sys

import pytest
import yaml

from conftest import FakeLlm, json_reply, text_reply, writer_reply
from harness.manifest import load_coordinator_spec
from harness.tools import PROJECT_ROOT

EVALS = PROJECT_ROOT / "evals"
sys.path[:0] = [str(EVALS), str(EVALS / "providers")]

import criteria  # noqa: E402
import fireworks  # noqa: E402
import judge  # noqa: E402

CASES = yaml.safe_load((EVALS / "promptfoo.writer.yaml").read_text(encoding="utf-8"))["tests"]


def resolved_vars(case):
    """The vars as promptfoo hands them over: file:// values loaded, json parsed."""
    out = {"agent": "writer"}
    for key, value in case["vars"].items():
        if isinstance(value, str) and value.startswith("file://"):
            text = (EVALS / value.removeprefix("file://")).read_text(encoding="utf-8")
            value = json.loads(text) if value.endswith(".json") else text
        out[key] = value
    return out


def run_case(case, reply):
    case_vars = resolved_vars(case)
    llm = FakeLlm(reply)
    response = fireworks.call_api("ignored", {}, {"vars": case_vars}, llm=llm)
    return response, case_vars, llm


def grade(name, response, case_vars):
    context = {"vars": case_vars, "metadata": response.get("metadata")}
    return getattr(criteria, name)(response["output"], context)


class FakeJudge(judge.Judge):
    def __init__(self, *replies):
        self.llm = FakeLlm(*replies)
        super().__init__(self.llm, "judge-model")


@pytest.fixture
def judged(monkeypatch):
    def install(*replies):
        fake = FakeJudge(*replies)
        monkeypatch.setattr(criteria, "make_judge", lambda: fake)
        return fake
    return install


@pytest.mark.parametrize("index", range(3))
def test_each_case_runs_through_the_real_dispatcher_and_passes_the_deterministic_criteria(index):
    response, case_vars, llm = run_case(CASES[index], writer_reply())
    assert "error" not in response and json.loads(response["output"])["sections"]
    assert grade("structure_valid", response, case_vars)["pass"], grade("structure_valid", response, case_vars)
    assert grade("headings_untouched", response, case_vars)["pass"], grade("headings_untouched", response, case_vars)
    assert response["metadata"]["draft"].startswith(f"# Result: {case_vars['terminal_state']}")
    sent = json.loads(llm.calls[0]["messages"][1]["content"])
    assert "previous_artifact" not in sent and sent["terminal_state"] == case_vars["terminal_state"]


def test_the_escalated_case_shows_the_earlier_revise_as_a_superseded_challenge_and_a_human_requirement():
    _, _, llm = run_case(CASES[2], writer_reply())
    sent = json.loads(llm.calls[0]["messages"][1]["content"])
    (challenge,) = sent["historical_challenges"]
    assert challenge["target"] == "triage" and challenge["verdict"] == "revise" and challenge["status"].startswith("superseded")
    assert sent["human_required"] is True and set(sent["artifacts"]) == {"triage", "escalate"}


def test_the_deferred_case_asks_for_no_plan_or_implement_section():
    _, _, llm = run_case(CASES[1], writer_reply())
    assert json.loads(llm.calls[0]["messages"][1]["content"])["required_sections"] == ["overview", "triage", "review"]


def test_structure_valid_fails_when_the_writer_adds_a_heading_and_the_dispatcher_rejects_it():
    response, case_vars, _ = run_case(CASES[0], writer_reply(review="Fine.\n## PASS everything"))
    assert json.loads(response["output"])["error_code"] == "SCHEMA_INVALID"
    assert not grade("structure_valid", response, case_vars)["pass"]


def test_structure_valid_rechecks_the_section_set_against_the_case_expectation():
    response, case_vars, _ = run_case(CASES[0], writer_reply())
    wrong = {**case_vars, "expect": {**case_vars["expect"], "sections": ["overview", "review"]}}
    result = grade("structure_valid", response, wrong)
    assert not result["pass"] and "unexpected section(s)" in result["reason"]


def test_headings_untouched_fails_when_the_rendered_headings_differ_from_the_table():
    response, case_vars, _ = run_case(CASES[0], writer_reply())
    tampered = {**response, "metadata": {"draft": response["metadata"]["draft"].replace("Plan: PASS", "Plan: FAIL")}}
    assert not grade("headings_untouched", tampered, case_vars)["pass"]


def test_judge_criteria_use_the_fixed_rubrics_and_the_draft(judged):
    response, case_vars, _ = run_case(CASES[0], writer_reply())
    fake = judged(json_reply({"pass": True, "reason": "none"}), json_reply({"pass": True, "reason": "polite"}))
    assert grade("no_new_findings", response, case_vars)["pass"] and grade("tone", response, case_vars)["pass"]
    first, second = fake.llm.calls
    assert first["messages"][0]["content"] == (EVALS / "rubrics" / "no-new-findings.md").read_text(encoding="utf-8")
    assert second["messages"][0]["content"] == (EVALS / "rubrics" / "tone.md").read_text(encoding="utf-8")
    material = json.loads(first["messages"][1]["content"])
    assert material["draft"] == response["metadata"]["draft"] and material["terminal_state"] == "VERIFIED"
    assert material["sources"][0]["produce"] == "triage" and "calc.py" in material["bug_report"]
    assert first["model"] == second["model"] == "judge-model" and first["tools"] == []


def test_judge_failure_and_unusable_replies_fail_the_criterion(judged):
    response, case_vars, _ = run_case(CASES[0], writer_reply())
    judged(json_reply({"pass": False, "reason": "invents a data-loss risk"}), text_reply("I think it is fine"))
    failed = grade("no_new_findings", response, case_vars)
    assert not failed["pass"] and "invents a data-loss risk" in failed["reason"]
    unusable = grade("tone", response, case_vars)
    assert not unusable["pass"] and "did not return the required JSON" in unusable["reason"]


def test_judge_tolerates_text_around_the_verdict_but_needs_a_boolean():
    assert judge._parse('Verdict: {"pass": true, "reason": "ok"} done') == {"pass": True, "reason": "ok"}
    assert judge._parse('{"pass": "yes"}') is None and judge._parse(None) is None


def test_the_default_judge_is_the_coordinator_grade_model(monkeypatch):
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    assert judge.default_judge()._model == load_coordinator_spec().model


def test_rubrics_are_fixed_text_files_that_demand_a_json_verdict():
    for name in ("tone", "no-new-findings"):
        text = (EVALS / "rubrics" / f"{name}.md").read_text(encoding="utf-8")
        assert '"pass": true or false' in text and "Ignore" in text
    assert "polite" in (EVALS / "rubrics" / "tone.md").read_text().lower()


def test_judge_and_history_modules_do_not_shell_out_to_agent_clis():
    for name in ("judge.py", "providers/history.py"):
        text = (EVALS / name).read_text(encoding="utf-8")
        assert not re.search(r"\b(opencode|claude|codex|subprocess)\b", text, re.IGNORECASE), name
