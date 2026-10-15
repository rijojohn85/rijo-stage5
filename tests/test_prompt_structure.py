"""Structural checks on every system prompt: catches a prompt that lost its Output or Rules section."""
import json
import re

import pytest

from harness.tools import PROJECT_ROOT

PROMPTS = PROJECT_ROOT / "prompts"
SCHEMAS = PROJECT_ROOT / "schemas"
PRODUCERS = {"triage": "triage", "plan": "plan", "implement": "implement", "escalate": "escalation"}
ALL_PROMPTS = {**PRODUCERS, "review": "review", "writer": "writer"}
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


WRITER_INPUT_KEYS = ("terminal_state", "reason", "required_sections", "artifacts", "reviews",
                     "historical_challenges", "human_required", "human_reasons", "writer_feedback")


def test_writer_prompt_documents_every_input_the_harness_sends():
    text = prompt_text("writer")
    for key in WRITER_INPUT_KEYS:
        assert f"`{key}`" in text, f"writer.md does not mention {key!r}"
    assert text.index("`writer_feedback`") < text.index("## Output")


def test_writer_prompt_is_never_shown_its_previous_draft_and_says_so():
    text = prompt_text("writer")
    assert "previous_artifact" not in text and "never see the earlier" in text


def test_writer_prompt_forbids_headings_labels_and_new_findings():
    text = prompt_text("writer").lower()
    for phrase in ("never write a heading", "start with `#`", "pass or fail", "add no new findings"):
        assert phrase in text, phrase


def test_writer_prompt_names_every_schema_section_key():
    schema = json.loads((SCHEMAS / "writer.json").read_text(encoding="utf-8"))
    text = prompt_text("writer")
    for key in schema["properties"]["sections"]["propertyNames"]["enum"]:
        assert f'"{key}"' in text, f"writer.md example lacks section {key!r}"


def test_review_prompt_describes_the_writer_draft_inputs():
    text = prompt_text("review")
    for key in ("draft", "reviews", "historical_challenges", "human_required"):
        assert f"`{key}`" in text


def test_coordinator_prompt_tells_it_to_retry_a_failed_writer_with_the_error_detail():
    text = prompt_text("coordinator")
    assert "launch_writer` itself returns an error" in text


def test_review_prompt_bounds_read_file_use_without_hardcoded_filenames():
    text = prompt_text("review")
    paragraph = text.split("You can also call `read_file`", 1)[1].split("## What to check", 1)[0]
    for filename in ("calc.py", "storage.py", "SPEC.md"):
        assert filename not in text
    assert "at most 3 `read_file` calls" in paragraph
    assert "each at most once" in paragraph
    assert "no further tool call" in paragraph
    assert "reply with the verdict JSON" in paragraph
