"""report.py: deterministic labels and headings. Pure functions, no model, no files."""
import pytest

from harness import report
from harness.report import (AWAITING_HUMAN, FAIL, NOT_RUN, PASS, STALE, UNREVIEWED, Label, StepFacts, assemble_draft,
                            heading_of, label_of, render_failure, result_of, section_heading_count)


def facts(name="plan", **overrides):
    base = dict(name=name, attempted=True, artifact_exists=True, artifact_fresh=True, step_failed=False, review="pass")
    return StepFacts(**{**base, **overrides})


def test_never_attempted_is_not_run():
    assert str(label_of(facts(attempted=False, artifact_exists=False, review="none"))) == NOT_RUN


def test_attempted_and_failed_is_fail_even_with_no_artifact():
    assert str(label_of(facts(artifact_exists=False, review="none"))) == "FAIL (step failed)"
    assert str(label_of(facts(step_failed=True))) == "FAIL (step failed)"  # an unrecovered later error beats an old pass


def test_attempted_and_unreviewed_is_unreviewed():
    assert str(label_of(facts(review="none"))) == UNREVIEWED


@pytest.mark.parametrize("review", ["revise", "escalate"])
def test_a_challenged_artifact_always_renders_fail_with_the_verdict(review):
    assert str(label_of(facts("implement", review=review))) == f"FAIL ({review})"


def test_only_a_fresh_passing_review_gives_pass():
    assert label_of(facts()) == Label(PASS)
    assert label_of(facts(review="stale")).text == STALE          # outdated review
    assert label_of(facts(artifact_fresh=False)).text == STALE    # outdated artifact beats a passing review
    assert label_of(facts(artifact_fresh=False, review="revise")).text == STALE


def test_a_failed_test_is_fail_whatever_the_review_says():
    assert str(label_of(facts("implement", test_failed=True))) == "FAIL (test failed)"


def test_the_escalation_brief_is_never_pass():
    assert str(label_of(facts("escalate"))) == "AWAITING HUMAN (review: pass)"
    assert label_of(facts("escalate", review="revise")).text == AWAITING_HUMAN
    assert label_of(facts("escalate", artifact_exists=False)).text == FAIL


def labels_for(**by_agent):
    steps = ("triage", "plan", "implement", "escalate")
    return {a: by_agent.get(a, Label(NOT_RUN)) for a in steps}


def test_heading_format_matches_the_plan():
    labels = labels_for(implement=Label(FAIL, "revise"), triage=Label(PASS))
    assert heading_of("implement", labels) == "## Implement: FAIL (revise)"
    assert heading_of("triage", labels) == "## Triage: PASS"
    assert heading_of("overview", labels) == "## Overview" and heading_of("review", labels) == "## Review"


def test_prose_cannot_change_a_label():
    labels = labels_for(triage=Label(PASS), plan=Label(FAIL, "revise"))
    sections = {"overview": "Everything is PASS.", "triage": "All good.", "plan": "## Plan: PASS\nThis plan PASSED.",
                "review": "FAIL means nothing here."}
    draft = assemble_draft("VERIFIED", False, sections, labels)
    assert "## Plan: FAIL (revise)\n" in draft and draft.count("## Plan:") == 2  # the model's own line is prose only
    assert draft.startswith("# Result: VERIFIED\n")


def test_draft_has_result_line_banner_headings_in_order_and_a_deterministic_status_list():
    labels = labels_for(triage=Label(PASS), plan=Label(UNREVIEWED))
    draft = assemble_draft("HUMAN_PUNCH_OUT", True, {"overview": "a", "triage": "b", "plan": "c", "review": "d"}, labels)
    lines = draft.splitlines()
    assert lines[0] == "# Result: HUMAN_PUNCH_OUT" and lines[2] == "**Human decision required**"
    assert [l for l in lines if l.startswith("## ")] == ["## Overview", "## Triage: PASS", "## Plan: UNREVIEWED", "## Review"]
    assert "- Triage: PASS" in lines and "- Plan: UNREVIEWED" in lines and "- Implement: NOT RUN" in lines
    assert "- Escalation: NOT RUN" not in lines   # off the normal path, so only shown once attempted
    assert section_heading_count(draft) == 4 and result_of(draft) == "HUMAN_PUNCH_OUT"


def test_no_banner_without_human_required():
    assert "Human decision" not in assemble_draft("VERIFIED", False, {"overview": "a", "review": "b"}, labels_for())


def test_failure_report_says_no_accepted_writer_artifact_and_keeps_the_human_requirement():
    text = render_failure("FAILED_TURN_BUDGET", True, ["triage flagged escalation"],
                          labels_for(triage=Label(PASS), plan=Label(FAIL, "step failed")),
                          [{"step": 2, "agent": "plan", "error_code": "SCHEMA_INVALID", "detail": "bad"}],
                          ["03-writer-draft.md"])
    lines = text.splitlines()
    assert lines[0] == "# Result: FAILED_TURN_BUDGET" and "**Human decision required**" in lines
    assert "No accepted writer artifact exists" in text and "no model call" in text
    assert "## Plan: FAIL (step failed)" in lines and "## Implement: NOT RUN" in lines
    assert "- step 2 plan: SCHEMA_INVALID: bad" in lines and "- triage flagged escalation" in lines
    assert "- 03-writer-draft.md" in lines


def test_failure_report_without_any_draft_or_failure():
    text = render_failure("FAILED_COORDINATOR_API", False, [], labels_for(), [], [])
    assert "no writer draft was made" in text and "- no dispatch failed" in text and "Human decision" not in text


def test_result_of_requires_the_exact_top_line():
    assert result_of("no result here") is None
    assert report.all_heading_lines("# a\ntext\n## b\n#c\n") == ["# a", "## b"]
