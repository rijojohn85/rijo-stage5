"""Structural checks on every system prompt: catches a prompt that lost its Output or Rules section."""
import json
import re

import pytest

from harness.tools import PROJECT_ROOT

PROMPTS = PROJECT_ROOT / "prompts"
SCHEMAS = PROJECT_ROOT / "schemas"
PRODUCERS = {"triage": "triage", "plan": "plan", "implement": "implement", "escalate": "escalation"}
ALL_PROMPTS = {**PRODUCERS, "review": "review"}
REVIEW_INPUT_KEYS = ("review_target", "echo_these_fields_in_your_reply")


def prompt_text(name):
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def required_keys(schema_name):
    return json.loads((SCHEMAS / f"{schema_name}.json").read_text(encoding="utf-8"))["required"]


def headings(text):
    return re.findall(r"^#{1,3} (.+)$", text, re.MULTILINE)


@pytest.mark.parametrize("name", ALL_PROMPTS)
def test_prompt_has_input_output_and_rules_sections_in_order(name):
    found = headings(prompt_text(name))
    for section in ("Inputs", "Output", "Rules"):
        assert section in found, f"{name}.md lost its {section!r} section"
    assert found.index("Inputs") < found.index("Output") < found.index("Rules")


@pytest.mark.parametrize("name,schema_name", ALL_PROMPTS.items())
def test_prompt_names_every_required_schema_key(name, schema_name):
    text = prompt_text(name)
    for key in required_keys(schema_name):
        assert f'"{key}"' in text or f"`{key}`" in text, f"{name}.md does not mention {key!r}"


@pytest.mark.parametrize("name", ALL_PROMPTS)
def test_prompt_has_no_leftover_placeholders_and_is_not_truncated(name):
    text = prompt_text(name)
    assert not re.search(r"\{\{.*?\}\}", text) and "TODO" not in text
    assert text.endswith("\n") and not text.rstrip().endswith((",", ":", "and", "the"))
    assert len(text.splitlines()) > 20


@pytest.mark.parametrize("name", PRODUCERS)
def test_producer_prompt_ends_with_the_reviewer_findings_section(name):
    text = prompt_text(name)
    found = headings(text)
    assert found[-1] == "Reviewer findings (optional)"
    assert text.count("reviewer_feedback") >= 2  # listed under Inputs and explained in the section


def test_review_prompt_describes_its_binding_inputs():
    text = prompt_text("review")
    for key in REVIEW_INPUT_KEYS:
        assert key in text


@pytest.mark.parametrize("name", PRODUCERS)
def test_producer_prompt_documents_previous_artifact(name):
    text = prompt_text(name)
    assert text.count("previous_artifact") >= 2  # listed under Inputs and explained in the findings section
    assert text.index("previous_artifact") < text.index("## Output")


def test_coordinator_prompt_has_sections_in_order_and_is_not_truncated():
    text = prompt_text("coordinator")
    found = headings(text)
    assert found.index("Inputs") < found.index("Output") < found.index("Rules")
    assert text.endswith("\n") and len(text.splitlines()) > 20
    assert not re.search(r"\{\{.*?\}\}", text) and "TODO" not in text


def test_coordinator_prompt_names_every_launch_tool_and_terminal_state():
    from harness.launch_tools import LAUNCH_SPECS, TERMINAL_STATES
    text = prompt_text("coordinator")
    for name in LAUNCH_SPECS:
        assert f"`{name}`" in text, f"coordinator.md does not mention {name}"
    for state in TERMINAL_STATES:
        assert state in text


def test_coordinator_prompt_tells_it_to_pass_the_stop_reason_as_escalate_feedback():
    text = prompt_text("coordinator")
    assert "feedback" in text and "launch_escalate" in text and "run_state" in text
