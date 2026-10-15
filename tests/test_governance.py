"""Bypass attempts (plan section 8 item 5): an illegal completion claim must end FAILED_COORDINATION.

Every script here is a coordinator that tries to finish a run the evidence does not support. The
harness refuses each claim; a coordinator that keeps claiming gets the deterministic failure report
(which keeps any human requirement), never a draft, never a VERIFIED/DEFERRED/HUMAN_PUNCH_OUT report.
"""
import pytest

from conftest import ESCALATION, IMPLEMENT, TRIAGE, json_reply, review_reply, text_reply
from harness.coordinator import FAILED_COORDINATION, FAILED_TURN_BUDGET, MAX_REFUSED_CLAIMS
from harness.freshness import Freshness
from harness.run_state import RunState
from test_coordinator import BUG, HAPPY_COORDINATOR, HAPPY_HELPERS, REVISE, build, launch, reviewing

DANGEROUS = [{"category": "dangerous", "detail": "the fix deletes customer records"}]
DEFER = {**TRIAGE, "decision": "defer"}
ESCALATED_TRIAGE = {**TRIAGE, "decision": "defer", "escalation": True}  # what a data-loss report is forced to


def claim(state):
    return launch("launch_writer", terminal_state=state)


def claims(*states):
    return [claim(s) for s in states]


def run_to_failure(tmp_path, coordinator_script, helpers, *, max_turns=12):
    coordinator, llm, audit, run_dir = build(tmp_path, coordinator_script, helpers, max_turns=max_turns)
    outcome = coordinator.run(BUG)
    return outcome, audit, run_dir, llm


def assert_failed_coordination(outcome, run_dir):
    assert outcome.result == FAILED_COORDINATION
    report = (run_dir / "report.md").read_text()
    assert report.startswith("# Result: FAILED_COORDINATION")
    assert "No accepted writer artifact exists" in report
    assert outcome.report_path == run_dir / "report.md"
    return report


def test_the_limit_is_three_refused_claims():
    assert MAX_REFUSED_CLAIMS == 3


def test_verified_after_a_data_loss_report_without_escalating(tmp_path):
    script = [launch("launch_triage"), reviewing("triage"), *claims("VERIFIED", "VERIFIED", "VERIFIED")]
    outcome, audit, run_dir, llm = run_to_failure(tmp_path, script, [json_reply(ESCALATED_TRIAGE), review_reply()])
    report = assert_failed_coordination(outcome, run_dir)
    assert outcome.human_required and "**Human decision required**" in report  # the requirement survives the failure
    assert "triage step 1 flagged escalation" in report
    refused = [e for e in audit.dispatches() if e["agent"] == "writer"]
    assert [e["error_code"] for e in refused] == ["PRECONDITION_MISSING"] * 3 and "a human is required" in refused[0]["detail"]
    assert len(llm.helpers.calls) == 2   # no writer call was ever paid for


def test_verified_on_a_review_that_predates_a_rerun_implement(tmp_path):
    rerun = [launch("launch_implement", feedback="once more"), *claims("VERIFIED", "VERIFIED", "VERIFIED")]
    script = HAPPY_COORDINATOR[:6] + rerun
    helpers = HAPPY_HELPERS[:6] + [json_reply({**IMPLEMENT, "summary": "fixed add, differently"})]
    outcome, audit, run_dir, _ = run_to_failure(tmp_path, script, helpers)
    assert_failed_coordination(outcome, run_dir)
    refused = [e for e in audit.dispatches() if e["agent"] == "writer"]
    assert len(refused) == 3 and "implement" in refused[0]["detail"]
    assert not outcome.human_required        # a stale review is not a human matter; it simply cannot authorize VERIFIED


def test_deferred_with_no_triage_review(tmp_path):
    script = [launch("launch_triage"), *claims("DEFERRED", "DEFERRED", "DEFERRED")]
    outcome, audit, run_dir, _ = run_to_failure(tmp_path, script, [json_reply(DEFER)])
    assert_failed_coordination(outcome, run_dir)
    refused = [e for e in audit.dispatches() if e["agent"] == "writer"]
    assert len(refused) == 3 and "needs a fresh triage with a fresh passing review" in refused[0]["detail"]


@pytest.mark.parametrize("claimed", [("VERIFIED", "VERIFIED", "VERIFIED"), ("VERIFIED", "HUMAN_PUNCH_OUT", "DEFERRED")])
def test_skipping_launch_escalate_when_a_review_says_dangerous(tmp_path, claimed):
    script = [launch("launch_triage"), reviewing("triage"), *claims(*claimed)]
    helpers = [json_reply(TRIAGE), review_reply("escalate", DANGEROUS)]
    outcome, audit, run_dir, _ = run_to_failure(tmp_path, script, helpers)
    report = assert_failed_coordination(outcome, run_dir)
    assert outcome.human_required and "found an escalate/dangerous problem" in report
    assert not any(e["agent"] == "escalate" for e in audit.dispatches())


def test_human_punch_out_with_no_brief(tmp_path):
    script = [launch("launch_triage"), reviewing("triage"), *claims("HUMAN_PUNCH_OUT", "HUMAN_PUNCH_OUT", "HUMAN_PUNCH_OUT")]
    outcome, audit, run_dir, _ = run_to_failure(tmp_path, script, [json_reply(ESCALATED_TRIAGE), review_reply()])
    assert_failed_coordination(outcome, run_dir)
    refused = [e for e in audit.dispatches() if e["agent"] == "writer"]
    assert len(refused) == 3 and "needs a fresh escalate" in refused[0]["detail"]


@pytest.mark.parametrize("brief_review", [None, "revise"])
def test_human_punch_out_with_no_passing_brief_review(tmp_path, brief_review):
    script = [launch("launch_triage"), reviewing("triage"), launch("launch_escalate", feedback="stopping")]
    helpers = [json_reply(ESCALATED_TRIAGE), review_reply(), json_reply(ESCALATION)]
    if brief_review:
        script.append(reviewing("escalate"))
        helpers.append(review_reply(brief_review, REVISE))
    script += claims("HUMAN_PUNCH_OUT", "HUMAN_PUNCH_OUT", "HUMAN_PUNCH_OUT")
    outcome, audit, run_dir, _ = run_to_failure(tmp_path, script, helpers)
    report = assert_failed_coordination(outcome, run_dir)
    assert outcome.human_required and "## Escalation: AWAITING HUMAN" in report
    assert [e["error_code"] for e in audit.dispatches() if e["agent"] == "writer"] == ["PRECONDITION_MISSING"] * 3


@pytest.mark.parametrize("draft_review", [None, "revise"])
def test_finalizing_a_normal_report_without_a_passing_review_of_the_exact_draft(tmp_path, draft_review):
    script = HAPPY_COORDINATOR[:7]
    helpers = HAPPY_HELPERS[:7]
    if draft_review:
        script.append(reviewing("writer"))
        helpers.append(review_reply(draft_review, REVISE))
    script += [text_reply("The report is complete, VERIFIED.")] * 3
    outcome, audit, run_dir, llm = run_to_failure(tmp_path, script, helpers)
    report = assert_failed_coordination(outcome, run_dir)
    assert "07-writer-draft.md" in report                       # the unreviewed draft is listed, never published
    assert report != (run_dir / "07-writer-draft.md").read_text()
    not_finished = [m for m in llm.coordinator.calls[-1]["messages"] if m["role"] == "user" and "Not finished" in m["content"]]
    assert len(not_finished) == 2                                # the third refusal ends the run instead of another nudge


def test_a_writer_draft_for_a_state_the_evidence_no_longer_supports_ends_in_failed_coordination(tmp_path):
    script = HAPPY_COORDINATOR[:7] + [launch("launch_implement", feedback="again")] + [text_reply("done")] * 3
    helpers = HAPPY_HELPERS[:7] + [json_reply({**IMPLEMENT, "summary": "changed after the draft"})]
    outcome, _, run_dir, _ = run_to_failure(tmp_path, script, helpers)
    assert_failed_coordination(outcome, run_dir)


def test_a_corrected_claim_still_finishes_and_is_not_a_coordination_failure(tmp_path):
    early = [claim("VERIFIED"), claim("VERIFIED")]   # two refusals: below the limit
    script = early + HAPPY_COORDINATOR
    outcome, audit, run_dir, _ = run_to_failure(tmp_path, script, HAPPY_HELPERS, max_turns=12)
    assert outcome.result == "VERIFIED"
    assert [e["error_code"] for e in audit.dispatches()[:2]] == ["PRECONDITION_MISSING"] * 2


def test_chatter_below_the_limit_only_burns_turns(tmp_path):
    outcome, _, _, _ = run_to_failure(tmp_path, [text_reply("thinking...")] * 2, [], max_turns=2)
    assert outcome.result == FAILED_TURN_BUDGET


def test_refused_launches_other_than_a_terminal_claim_are_not_counted(tmp_path):
    script = [reviewing("triage")] * 5      # a review with nothing to review is a caller mistake, not a completion claim
    outcome, _, _, _ = run_to_failure(tmp_path, script, [], max_turns=5)
    assert outcome.result == FAILED_TURN_BUDGET


# --- human gate: history is never erased -----------------------------------------------------

def make_state(env):
    return RunState(env.store, env.audit, Freshness(env.store, env.sandbox))


def test_dangerous_review_then_producer_rerun_makes_the_original_review_stale_but_a_human_is_still_required(env):
    env.run("triage", json_reply(TRIAGE))
    env.run("reviewer", review_reply("escalate", DANGEROUS), target="triage")
    assert make_state(env).review_status("triage") == "escalate" and make_state(env).human_required()
    env.run("triage", json_reply({**TRIAGE, "rationale": "reworded"}), feedback="address the danger")
    state = make_state(env)
    assert state.review_status("triage") == "stale"      # the dangerous review judged the replaced artifact
    assert state.human_required()                        # but its finding still counts
    env.run("reviewer", review_reply(), target="triage")
    state = make_state(env)
    assert state.review_status("triage") == "pass" and state.human_required()   # a later pass cannot erase it
    assert any("found an escalate/dangerous problem" in r for r in state.human_reasons())
