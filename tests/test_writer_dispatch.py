"""The writer and the review of its draft, through the real dispatcher with a fake LLM."""
import json

import pytest

from conftest import ESCALATION, IMPLEMENT, PLAN, TRIAGE, json_reply, review_reply, text_reply, writer_reply
from harness.hashing import sha256_text
from harness.report_publisher import ReportPublisher
from harness.results import ErrorCode
from harness.run_views import RunViews

REVISE = [{"category": "incorrect", "detail": "the draft invents a defect"}]
SECTIONS = ("overview", "triage", "plan", "implement", "review")


def error_code(result):
    assert result["ok"] is False
    return result["error_code"]


def verified_ready(env):
    env.reviewed("triage", TRIAGE)
    env.reviewed("plan", PLAN)
    env.reviewed("implement", IMPLEMENT)


def write(env, reply=None, state="VERIFIED", **kwargs):
    return env.run("writer", reply or writer_reply(), terminal_state=state, **kwargs)


def writer_message(env, call=-1):
    return json.loads(env.llm.calls[call]["messages"][1]["content"])


def test_writer_saves_an_immutable_artifact_and_draft_with_code_made_headings(env):
    verified_ready(env)
    result = write(env)
    assert result["ok"] and set(result["artifact"]["sections"]) == set(SECTIONS)
    assert json.loads((env.run_dir / "07-writer.json").read_text()) == result["artifact"]
    draft = (env.run_dir / "07-writer-draft.md").read_text()
    assert [l for l in draft.splitlines() if l.startswith("#")] == [
        "# Result: VERIFIED", "## Overview", "## Triage: PASS", "## Plan: PASS", "## Implement: PASS", "## Review"]


def test_writer_gets_sources_reviews_reason_and_never_a_previous_artifact(env):
    verified_ready(env)
    write(env)
    first = writer_message(env)
    assert set(first) == {"bug_report", "terminal_state", "reason", "required_sections", "artifacts", "reviews",
                          "historical_challenges", "human_required", "human_reasons", "writer_feedback"}
    assert first["terminal_state"] == "VERIFIED" and first["human_required"] is False
    assert first["reviews"]["plan"] == {"target": "plan", "status": "pass", "verdict": "pass", "findings": []}
    assert first["artifacts"] == {"triage": TRIAGE, "plan": PLAN, "implement": IMPLEMENT}
    assert first["writer_feedback"] is None
    write(env, feedback="the draft invents a defect")
    again = writer_message(env)
    assert again["writer_feedback"] == "the draft invents a defect" and "previous_artifact" not in again
    assert "sections" not in json.dumps(again["artifacts"])  # the earlier draft is not in the evidence


def test_each_attempt_gets_its_own_files_and_the_first_is_untouched(env):
    verified_ready(env)
    write(env)
    first = (env.run_dir / "07-writer-draft.md").read_text()
    assert write(env, writer_reply(overview="A different overview."))["ok"]
    assert (env.run_dir / "07-writer-draft.md").read_text() == first
    assert "A different overview." in (env.run_dir / "08-writer-draft.md").read_text()
    assert error_code(write(env)) == ErrorCode.REVISION_CAP_REACHED  # initial draft plus one replacement


def test_an_illegal_terminal_state_is_refused_without_a_model_call(env):
    env.reviewed("triage", TRIAGE)
    for state in ("VERIFIED", "DEFERRED", "HUMAN_PUNCH_OUT", "NONSENSE"):
        assert error_code(write(env, state=state)) == ErrorCode.PRECONDITION_MISSING
    assert len(env.llm.calls) == 2  # only the triage and its review


@pytest.mark.parametrize("sections,detail", [
    ({"overview": "ok", "triage": "ok", "plan": "ok", "implement": "ok", "review": "   "}, "empty section(s): review"),
    ({"overview": "ok", "triage": "ok", "plan": "ok", "implement": "ok", "review": ""}, "empty section(s): review"),
    ({"overview": "ok", "triage": "ok", "plan": "ok", "implement": "ok", "review": "fine\n## Fake: PASS"},
     "line starting with '#'"),
    ({"overview": "ok", "triage": "ok", "plan": "ok", "implement": "ok", "review": "fine\n   # indented"},
     "line starting with '#'"),
    ({"overview": "ok", "triage": "ok", "plan": "ok", "review": "ok"}, "missing section(s): implement"),
    ({"overview": "ok", "triage": "ok", "plan": "ok", "implement": "ok", "review": "ok", "escalation": "x"},
     "unexpected section(s): escalation"),
    ({"overview": "ok", "triage": "ok", "plan": "ok", "implement": "ok", "review": "ok", "summary": "x"}, "summary"),
    ({"overview": "ok", "triage": "ok", "plan": "ok", "implement": "ok", "review": 5}, "review"),
])
def test_bad_sections_are_rejected_with_a_structured_error(env, sections, detail):
    verified_ready(env)
    result = write(env, json_reply({"sections": sections}))
    assert error_code(result) == ErrorCode.SCHEMA_INVALID and detail in result["detail"]
    assert set(result) == {"ok", "agent", "error_code", "detail"}
    assert env.store.latest("writer") is None and len(env.rejected_files()) == 1


def test_not_json_and_extra_envelope_keys_are_rejected(env):
    verified_ready(env)
    assert error_code(write(env, text_reply("Here is your report"))) == ErrorCode.NOT_JSON
    body = {"sections": {k: "ok" for k in SECTIONS}, "headings": ["x"]}
    assert error_code(write(env, json_reply(body))) == ErrorCode.SCHEMA_INVALID


def test_required_sections_follow_the_attempted_steps(env):
    env.run("triage", json_reply({**TRIAGE, "decision": "defer"}))
    env.run("reviewer", review_reply(), target="triage")
    assert write(env, state="DEFERRED", reply=writer_reply())["ok"]
    assert set(writer_message(env)["required_sections"]) == {"overview", "triage", "review"}
    draft = (env.run_dir / "03-writer-draft.md").read_text()
    assert "- Plan: NOT RUN" in draft and "- Implement: NOT RUN" in draft and "## Plan" not in draft.split("Review status")[0]


def test_an_attempted_step_that_failed_still_needs_a_section_and_renders_fail(env):
    env.reviewed("triage", TRIAGE)
    env.run("plan", text_reply("nope"))                      # attempted, no valid artifact (a model call was made)
    env.run("reviewer", review_reply("escalate", [{"category": "dangerous", "detail": "deletes data"}]), target="triage")
    env.run("escalate", json_reply(ESCALATION))
    env.run("reviewer", review_reply(), target="escalate")
    assert write(env, state="HUMAN_PUNCH_OUT")["ok"]
    message = writer_message(env)
    assert set(message["required_sections"]) == {"overview", "triage", "plan", "escalation", "review"}
    assert message["human_required"] is True and "escalate/dangerous" in message["reason"]
    draft = (env.run_dir / "07-writer-draft.md").read_text()
    assert "## Plan: FAIL (step failed)" in draft and "## Escalation: AWAITING HUMAN (review: pass)" in draft
    assert "**Human decision required**" in draft and "## Implement" not in draft.split("Review status")[0]


def test_historical_challenges_carry_a_resolution_label(env):
    env.run("triage", json_reply(TRIAGE))
    env.run("reviewer", review_reply("revise", REVISE), target="triage")
    env.run("triage", json_reply({**TRIAGE, "rationale": "reworded"}), feedback="fix")
    env.run("reviewer", review_reply(), target="triage")
    env.run("plan", json_reply(PLAN))   # builds on the reworded triage
    env.run("reviewer", review_reply(), target="plan")
    env.run("implement", json_reply(IMPLEMENT))
    env.run("reviewer", review_reply(), target="implement")
    assert write(env)["ok"]
    (challenge,) = writer_message(env)["historical_challenges"]
    assert challenge["target"] == "triage" and challenge["status"].startswith("superseded")


# --- review of the draft ---------------------------------------------------------------------

def test_reviewer_is_bound_to_the_exact_draft_and_sees_it(env):
    verified_ready(env)
    write(env)
    draft = (env.run_dir / "07-writer-draft.md").read_text()
    assert env.run("reviewer", review_reply(), target="writer")["ok"]
    seen = writer_message(env)
    assert seen["draft"] == draft and set(seen["artifacts"]) == {"triage", "plan", "implement"}
    assert seen["echo_these_fields_in_your_reply"]["draft_sha256"] == sha256_text(draft)
    assert seen["reviews"]["triage"]["status"] == "pass"
    saved = env.store.latest("reviewer", "writer")
    assert saved.binding["draft_sha256"] == sha256_text(draft)


def test_a_wrong_draft_hash_in_the_review_is_a_binding_mismatch(env):
    verified_ready(env)
    write(env)
    result = env.run("reviewer", review_reply(draft_sha256="0" * 64), target="writer")
    assert error_code(result) == ErrorCode.REVIEW_BINDING_MISMATCH and "draft_sha256" in result["detail"]


def test_a_review_of_an_older_draft_does_not_cover_the_newer_one(env):
    verified_ready(env)
    write(env)
    env.run("reviewer", review_reply(), target="writer")
    write(env, writer_reply(overview="Rewritten."))
    assert not RunViews.build(env.run_dir, env.sandbox).freshness.has_fresh_passing_review("writer")


def test_a_writer_review_cannot_happen_before_a_draft(env):
    assert error_code(env.run("reviewer", review_reply(), target="writer")) == ErrorCode.PRECONDITION_MISSING


# --- publication -----------------------------------------------------------------------------

def publisher(env):
    return ReportPublisher(env.run_dir, RunViews.build(env.run_dir, env.sandbox))


def draft_ready(env):
    verified_ready(env)
    write(env)
    return (env.run_dir / "07-writer-draft.md").read_text()


def test_nothing_is_accepted_until_the_exact_draft_passes_review(env):
    draft_ready(env)
    assert publisher(env).accepted_state() is None
    env.run("reviewer", review_reply("revise", REVISE), target="writer")
    assert publisher(env).accepted_state() is None
    env.run("reviewer", review_reply(), target="writer")
    assert publisher(env).accepted_state() == "VERIFIED"


def test_report_md_is_a_byte_copy_of_the_accepted_draft_written_once(env):
    draft = draft_ready(env)
    env.run("reviewer", review_reply(), target="writer")
    path, digest = publisher(env).publish_accepted()
    assert path == env.run_dir / "report.md" and path.read_text() == draft
    assert sha256_text(path.read_text()) == digest == sha256_text(draft)
    with pytest.raises(FileExistsError):
        publisher(env).publish_accepted()
    assert path.read_text() == draft


def test_a_change_in_the_evidence_after_the_draft_blocks_acceptance(env):
    draft_ready(env)
    env.run("reviewer", review_reply(), target="writer")
    env.run("reviewer", review_reply("revise", REVISE), target="plan")   # plan's review now says revise: the Plan heading would change
    assert publisher(env).accepted_state() is None


def test_failure_report_is_published_without_a_model_call(env):
    verified_ready(env)
    calls = len(env.llm.calls)
    path, _ = publisher(env).publish_failure("FAILED_TURN_BUDGET")
    text = path.read_text()
    assert text.startswith("# Result: FAILED_TURN_BUDGET") and "No accepted writer artifact exists" in text
    assert "## Implement: PASS" in text and len(env.llm.calls) == calls
