"""Substance gate and human decision log. Everything runs in tmp dirs; nothing here signs for a real human."""
import io
import json
import shutil
from pathlib import Path

import pytest

from conftest import FIXED_TIME, TRIAGE, FakeSandbox
from harness import tools
from harness.assessment_file import ASSESSMENT_FILE, save_assessment
from harness.decision_log import (CONTINUE, PUNCH_OUT, REJECT, SUBSTANCE, DecisionLog, binds_punch_out,
                                  binds_substance, make_decision)
from harness.decisions import DecisionDesk, DecisionError, Paths, main
from harness.disposition import FINAL, PENDING_HUMAN, REJECTED, RELEASED, compute_disposition, read_disposition
from harness.hashing import sha256_text
from harness.manifest import load_agent_specs
from harness.reviewer_identity import ReviewerError, validate_reviewer
from harness.store import ArtifactStore
from harness.substance import SubstanceError, is_elevated, load_assessment

ROOT = Path(__file__).resolve().parent.parent
REPORT = "# Result: HUMAN_PUNCH_OUT\n\n**Human decision required**\n"
RUN_ID = "20261015-131410"


def ratings(*names):
    return {f"w{i}": {"rating": r, "why": "because"} for i, r in enumerate(names)}


@pytest.fixture
def paths(tmp_path):
    (tmp_path / "runs").mkdir()
    shutil.copy(ROOT / "substance.json", tmp_path / "substance.json")
    shutil.copy(ROOT / "manifest.yaml", tmp_path / "manifest.yaml")
    return Paths(tmp_path / "runs", tmp_path / "decisions" / "decisions.jsonl",
                 tmp_path / "substance.json", tmp_path / "manifest.yaml")


def make_run(paths, run_id=RUN_ID, *, report=REPORT, escalated=True):
    """A stopped run directory; `escalated` makes triage flag escalation, so a human is required."""
    run_dir = paths.runs_dir / run_id
    ArtifactStore(run_dir).save(1, "triage", None, {**TRIAGE, "escalation": escalated}, {}, {})
    (run_dir / "audit.jsonl").write_text("", encoding="utf-8")
    if report is not None:
        (run_dir / "report.md").write_text(report, encoding="utf-8")
    return run_dir


def snapshot(run_dir):
    return {p.name: p.read_bytes() for p in run_dir.iterdir() if p.name != "disposition.json"}


def desk_for(paths, answer=True, out=None):
    asked = []
    desk = DecisionDesk(paths, clock=lambda: FIXED_TIME, confirm=lambda prompt: asked.append(prompt) or answer,
                        out=out or io.StringIO(), sandbox_for=FakeSandbox)
    return desk, asked


def log_lines(paths):
    return DecisionLog(paths.decisions_path).entries()


# --- substance.json and elevation ------------------------------------------------------------

def test_shipped_substance_matches_the_manifest_and_elevates():
    assessment = load_assessment()
    assert set(assessment.workflows) == {a.workflow for a in load_agent_specs().values()}
    assert {w for w, e in assessment.workflows.items() if e["rating"] == "core"} == {"screen", "triage", "plan", "implement", "review"}
    assert assessment.elevated is True


@pytest.mark.parametrize("names,elevated", [
    (("core", "core"), False), (("core", "peripheral"), False), (("core", "peripheral", "peripheral"), True),
    (("peripheral", "peripheral", "peripheral"), True), (("core", "toy"), True), (("toy",), True)])
def test_elevation_rule(names, elevated):
    assert is_elevated(ratings(*names)) is elevated


def test_substance_must_list_exactly_the_manifest_workflows(paths):
    data = json.loads(paths.substance_path.read_text())
    del data["workflows"]["writer"]
    paths.substance_path.write_text(json.dumps(data))
    with pytest.raises(SubstanceError, match="must equal the manifest"):
        load_assessment(paths.substance_path, paths.manifest_path)
    data["workflows"].update(writer={"rating": "core", "why": "x"}, extra={"rating": "core", "why": "x"})
    paths.substance_path.write_text(json.dumps(data))
    with pytest.raises(SubstanceError):
        load_assessment(paths.substance_path, paths.manifest_path)


def test_substance_schema_rejects_bad_rating_and_empty_reason(paths):
    for bad in ({"rating": "huge", "why": "x"}, {"rating": "core", "why": "  "}, {"rating": "core"}):
        data = json.loads(paths.substance_path.read_text())
        data["workflows"]["plan"] = bad
        paths.substance_path.write_text(json.dumps(data))
        with pytest.raises(SubstanceError, match="invalid"):
            load_assessment(paths.substance_path, paths.manifest_path)


# --- disposition truth table -----------------------------------------------------------------

@pytest.mark.parametrize("required,expected,missing", [
    ({}, FINAL, []),
    ({SUBSTANCE: CONTINUE}, RELEASED, []),
    ({SUBSTANCE: None}, PENDING_HUMAN, [SUBSTANCE]),
    ({SUBSTANCE: REJECT}, REJECTED, []),
    ({SUBSTANCE: CONTINUE, PUNCH_OUT: CONTINUE}, RELEASED, []),
    ({SUBSTANCE: CONTINUE, PUNCH_OUT: None}, PENDING_HUMAN, [PUNCH_OUT]),
    ({SUBSTANCE: None, PUNCH_OUT: CONTINUE}, PENDING_HUMAN, [SUBSTANCE]),
    ({SUBSTANCE: None, PUNCH_OUT: None}, PENDING_HUMAN, [SUBSTANCE, PUNCH_OUT]),
    ({SUBSTANCE: REJECT, PUNCH_OUT: CONTINUE}, REJECTED, []),
    ({SUBSTANCE: CONTINUE, PUNCH_OUT: REJECT}, REJECTED, []),
    ({SUBSTANCE: REJECT, PUNCH_OUT: None}, REJECTED, [PUNCH_OUT]),  # reject wins over a missing gate
])
def test_disposition_truth_table(required, expected, missing):
    assert compute_disposition(required) == (expected, missing)


def test_final_when_nothing_is_required(paths):
    data = json.loads(paths.substance_path.read_text())
    data["workflows"]["escalate"]["rating"] = data["workflows"]["writer"]["rating"] = "core"
    paths.substance_path.write_text(json.dumps(data))
    make_run(paths, escalated=False)
    desk, _ = desk_for(paths)
    disposition = desk.status(RUN_ID)
    assert disposition["disposition"] == FINAL and disposition["required"] == []


# --- recording decisions ---------------------------------------------------------------------

def test_pending_then_released_with_report_and_artifacts_byte_identical(paths):
    run_dir = make_run(paths)
    before = snapshot(run_dir)
    desk, _ = desk_for(paths)
    pending = desk.status(RUN_ID)
    assert pending["disposition"] == PENDING_HUMAN and pending["missing"] == [SUBSTANCE, PUNCH_OUT]
    assert read_disposition(run_dir)["disposition"] == PENDING_HUMAN
    desk.sign_substance(CONTINUE, "Rijo John", "ok")
    assert desk.status(RUN_ID)["missing"] == [PUNCH_OUT]
    desk.sign_punch_out(RUN_ID, CONTINUE, "Rijo John", "ok")
    released = read_disposition(run_dir)
    assert released["disposition"] == RELEASED and released["missing"] == []
    assert released["execution_result"] == "HUMAN_PUNCH_OUT"  # the execution result is shown, not replaced
    assert snapshot(run_dir) == before
    assert [e["kind"] for e in log_lines(paths)] == [SUBSTANCE, PUNCH_OUT]


def test_substance_decision_retains_the_assessment_verbatim(paths):
    desk, _ = desk_for(paths)
    desk.sign_substance(CONTINUE, "Rijo John")
    [line] = log_lines(paths)
    assessment = load_assessment(paths.substance_path, paths.manifest_path)
    assert line["binding"]["assessment"] == assessment.as_record()
    assert line["binding"]["binding_sha256"] == assessment.binding_sha256
    assert line["reviewer"] == "Rijo John" and line["ts"] == FIXED_TIME.isoformat()


def test_reject_wins_and_latest_decision_per_binding_wins(paths):
    make_run(paths)
    desk, _ = desk_for(paths)
    desk.sign_substance(CONTINUE, "Rijo John")
    desk.sign_punch_out(RUN_ID, REJECT, "Rijo John")
    assert desk.status(RUN_ID)["disposition"] == REJECTED
    desk.sign_punch_out(RUN_ID, CONTINUE, "Rijo John")  # a later line for the same binding replaces it
    assert desk.status(RUN_ID)["disposition"] == RELEASED
    desk.sign_substance(REJECT, "Rijo John")
    assert desk.status(RUN_ID)["disposition"] == REJECTED
    assert len(log_lines(paths)) == 4


def test_log_is_append_only(paths):
    make_run(paths)
    desk, _ = desk_for(paths)
    sizes, contents = [], []
    for decision in (CONTINUE, REJECT, CONTINUE):
        desk.sign_substance(decision, "Rijo John")
        text = paths.decisions_path.read_text()
        contents.append(text)
        sizes.append(len(text))
    assert sizes == sorted(set(sizes))
    assert all(later.startswith(earlier) for earlier, later in zip(contents, contents[1:]))
    desk.status(RUN_ID)
    assert paths.decisions_path.read_text() == contents[-1]  # status never writes a decision


def test_editing_substance_or_manifest_invalidates_the_substance_decision(paths):
    make_run(paths)
    desk, _ = desk_for(paths)
    desk.sign_substance(CONTINUE, "Rijo John")
    assert SUBSTANCE not in desk.status(RUN_ID)["missing"]
    paths.manifest_path.write_text(paths.manifest_path.read_text() + "\n# edited\n")
    assert SUBSTANCE in desk.status(RUN_ID)["missing"]
    shutil.copy(ROOT / "manifest.yaml", paths.manifest_path)
    assert SUBSTANCE not in desk.status(RUN_ID)["missing"]
    data = json.loads(paths.substance_path.read_text())
    data["workflows"]["plan"]["why"] = "changed reason"
    paths.substance_path.write_text(json.dumps(data))
    assert SUBSTANCE in desk.status(RUN_ID)["missing"]


def test_punch_out_binds_to_run_and_report_hash(tmp_path):
    ok = binds_punch_out("a", "h")
    assert ok({"run_id": "a", "report_sha256": "h"})
    assert not ok({"run_id": "b", "report_sha256": "h"}) and not ok({"run_id": "a", "report_sha256": "x"})


def test_punch_out_for_another_run_or_an_old_report_hash_does_not_count(paths):
    run_dir = make_run(paths)
    make_run(paths, "other")
    desk, _ = desk_for(paths)
    desk.sign_substance(CONTINUE, "Rijo John")
    desk.sign_punch_out("other", CONTINUE, "Rijo John")
    assert desk.status(RUN_ID)["missing"] == [PUNCH_OUT]
    desk.sign_punch_out(RUN_ID, CONTINUE, "Rijo John")
    assert desk.status(RUN_ID)["disposition"] == RELEASED
    (run_dir / "report.md").write_text(REPORT + "tampered\n")  # a different report hash
    assert desk.status(RUN_ID)["missing"] == [PUNCH_OUT]


def test_punch_out_refused_for_unstopped_unknown_or_not_required_runs(paths):
    make_run(paths, "running", report=None)
    make_run(paths, "calm", escalated=False)
    desk, _ = desk_for(paths)
    for run_id in ("running", "nosuch", "calm"):
        with pytest.raises(DecisionError):
            desk.sign_punch_out(run_id, CONTINUE, "Rijo John")
    assert not paths.decisions_path.exists()


def test_a_failure_report_released_stays_failed(paths):
    make_run(paths, report="# Result: FAILED_TURN_BUDGET\n")
    desk, _ = desk_for(paths)
    desk.sign_substance(CONTINUE, "Rijo John")
    desk.sign_punch_out(RUN_ID, CONTINUE, "Rijo John")
    disposition = desk.status(RUN_ID)
    assert disposition["disposition"] == RELEASED and disposition["execution_result"] == "FAILED_TURN_BUDGET"


def test_resolving_either_gate_alone_leaves_the_other_pending(paths):
    make_run(paths)
    desk, _ = desk_for(paths)
    desk.sign_punch_out(RUN_ID, CONTINUE, "Rijo John")          # punch-out first: substance is still missing
    first = desk.status(RUN_ID)
    assert first["disposition"] == PENDING_HUMAN and first["missing"] == [SUBSTANCE]
    desk.sign_substance(CONTINUE, "Rijo John")
    assert desk.status(RUN_ID)["disposition"] == RELEASED


def test_a_rejection_prevents_release_even_while_the_other_gate_is_pending(paths):
    make_run(paths)
    desk, _ = desk_for(paths)
    desk.sign_punch_out(RUN_ID, REJECT, "Rijo John")
    rejected = desk.status(RUN_ID)
    assert rejected["disposition"] == REJECTED and rejected["missing"] == [SUBSTANCE]
    desk.sign_substance(CONTINUE, "Rijo John")
    assert desk.status(RUN_ID)["disposition"] == REJECTED         # the other gate passing does not release it


def test_approving_a_coordination_failure_report_does_not_make_it_verified(paths):
    make_run(paths, report="# Result: FAILED_COORDINATION\n\n**Human decision required**\n")
    before = snapshot(paths.runs_dir / RUN_ID)
    desk, _ = desk_for(paths)
    desk.sign_substance(CONTINUE, "Rijo John")
    desk.sign_punch_out(RUN_ID, CONTINUE, "Rijo John")
    disposition = desk.status(RUN_ID)
    assert disposition["disposition"] == RELEASED and disposition["execution_result"] == "FAILED_COORDINATION"
    assert snapshot(paths.runs_dir / RUN_ID) == before


def test_confirmation_declined_appends_nothing_and_confirmation_is_asked_before_appending(paths):
    make_run(paths)
    desk, asked = desk_for(paths, answer=False)
    with pytest.raises(DecisionError, match="not confirmed"):
        desk.sign_substance(CONTINUE, "Rijo John")
    with pytest.raises(DecisionError, match="not confirmed"):
        desk.sign_punch_out(RUN_ID, CONTINUE, "Rijo John")
    assert len(asked) == 2 and not paths.decisions_path.exists()


def test_punch_out_shows_the_report_and_every_reason(paths):
    make_run(paths)
    out = io.StringIO()
    desk, _ = desk_for(paths, out=out)
    desk.sign_punch_out(RUN_ID, CONTINUE, "Rijo John")
    shown = out.getvalue()
    assert REPORT in shown and "triage step 1 flagged escalation" in shown


def test_no_yes_flag_and_eof_declines(paths, monkeypatch, capsys):
    make_run(paths)
    with pytest.raises(SystemExit):
        main(["substance", "--decision", "continue", "--yes"])
    monkeypatch.setattr("builtins.input", lambda prompt: (_ for _ in ()).throw(EOFError()))
    from harness.decisions import type_yes
    assert type_yes("?") is False


# --- reviewer identity -----------------------------------------------------------------------

@pytest.mark.parametrize("name", ["", "   ", None, "Claude", "gpt-4", "my assistant", "Code Bot", "codex", "Gemini Pro",
                                  "AI", "llm-agent", "GPT4"])
def test_reviewer_rejects_empty_and_ai_identities(name):
    with pytest.raises(ReviewerError):
        validate_reviewer(name)


@pytest.mark.parametrize("name", ["Rijo John", "rijojohn", "Maria Aiello"])
def test_reviewer_accepts_human_names(name):
    assert validate_reviewer(name) == name


def test_signing_with_a_bad_reviewer_appends_nothing(paths):
    desk, asked = desk_for(paths)
    with pytest.raises(ReviewerError):
        desk.sign_substance(CONTINUE, "claude")
    assert not paths.decisions_path.exists() and asked == []


def test_cli_defaults_reviewer_to_user_env_and_reports_errors(paths):
    desk, _ = desk_for(paths)
    err = io.StringIO()
    assert main(["substance", "--decision", "continue"], desk=desk, env={"USER": "Rijo John"}, err=err) == 0
    assert log_lines(paths)[0]["reviewer"] == "Rijo John"
    assert main(["substance", "--decision", "continue"], desk=desk, env={}, err=err) == 2
    assert main(["substance", "--decision", "continue"], desk=desk, env={"USER": "claude"}, err=err) == 2
    assert len(log_lines(paths)) == 1 and "reviewer" in err.getvalue()


# --- no agent can write decisions ------------------------------------------------------------

def test_no_launch_tool_or_agent_tool_can_record_a_decision(tmp_path):
    from harness.launch_tools import launch_definitions
    from harness.manifest import load_coordinator_spec
    offered = {d["function"]["name"] for d in launch_definitions(load_coordinator_spec().tools)}
    every_tool = offered | set(tools.TOOLS) | {t for a in load_agent_specs().values() for t in a.tools}
    assert not [t for t in every_tool if any(word in t for word in ("decision", "sign", "substance", "punch"))]
    # the one tool that writes, jailed to sandbox/, cannot reach decisions/
    (tmp_path / "sandbox").mkdir()
    (tmp_path / "decisions").mkdir()
    log = tmp_path / "decisions" / "decisions.jsonl"
    log.write_text("x\n")
    for path in ("decisions/decisions.jsonl", "sandbox/../decisions/decisions.jsonl"):
        assert tools.edit_file(path, "x", "y", root=tmp_path)["ok"] is False
    assert log.read_text() == "x\n"


def test_only_the_cli_and_run_entry_point_use_the_decision_desk():
    users = {m.name for m in (ROOT / "harness").glob("*.py") if "harness.decisions" in m.read_text()}
    assert users <= {"decisions.py", "run.py"}  # run.py only borrows Paths; nothing under dispatch/coordination imports the desk
    writers = {m.name for m in (ROOT / "harness").glob("*.py") if "DecisionLog(" in m.read_text()}
    assert writers <= {"decisions.py", "run.py"}


# --- run.py writes the initial disposition ---------------------------------------------------

def run_clean(tmp_path, paths):
    from test_coordinator import BUG, HAPPY_COORDINATOR, HAPPY_HELPERS, SplitLlm
    from conftest import PLAN_FILE
    from harness.run import run_bug
    bug = tmp_path / "bug.md"
    bug.write_text(BUG, encoding="utf-8")
    (tmp_path / "project" / "sandbox").mkdir(parents=True)
    return run_bug(bug, llm=SplitLlm(HAPPY_COORDINATOR, HAPPY_HELPERS), runs_dir=paths.runs_dir,
                   tool_root=tmp_path / "project", sandbox=FakeSandbox(changed=[PLAN_FILE]),
                   clock=lambda: FIXED_TIME, stderr=io.StringIO(), paths=paths)


def test_run_writes_a_pending_disposition_when_elevated_and_unsigned(tmp_path, paths):
    run_dir, outcome = run_clean(tmp_path, paths)
    disposition = read_disposition(run_dir)
    assert outcome.result == "VERIFIED" and disposition["execution_result"] == "VERIFIED"
    assert disposition["disposition"] == PENDING_HUMAN and disposition["missing"] == [SUBSTANCE]


def test_run_is_released_once_the_substance_line_exists_and_report_is_unchanged(tmp_path, paths):
    run_dir, _ = run_clean(tmp_path, paths)
    report = (run_dir / "report.md").read_bytes()
    desk, _ = desk_for(paths)
    desk.sign_substance(CONTINUE, "Rijo John")
    assert desk.status(run_dir.name)["disposition"] == RELEASED
    assert (run_dir / "report.md").read_bytes() == report


def test_run_is_final_when_nothing_is_elevated(tmp_path, paths):
    data = json.loads(paths.substance_path.read_text())
    data["workflows"]["escalate"]["rating"] = data["workflows"]["writer"]["rating"] = "core"
    paths.substance_path.write_text(json.dumps(data))
    run_dir, _ = run_clean(tmp_path, paths)
    assert read_disposition(run_dir)["disposition"] == FINAL


# --- the run's own write-once substance assessment -------------------------------------------

def save_current_assessment(paths, run_dir):
    save_assessment(run_dir, load_assessment(paths.substance_path, paths.manifest_path))
    return run_dir / ASSESSMENT_FILE


def make_nothing_elevated(paths):
    data = json.loads(paths.substance_path.read_text())
    data["workflows"]["escalate"]["rating"] = data["workflows"]["writer"]["rating"] = "core"
    paths.substance_path.write_text(json.dumps(data))


def test_saved_assessment_keeps_a_run_pending_after_substance_is_edited(paths):
    run_dir = make_run(paths, escalated=False)
    saved = save_current_assessment(paths, run_dir)
    before = saved.read_bytes()
    desk, _ = desk_for(paths)
    assert desk.status(RUN_ID)["disposition"] == PENDING_HUMAN
    make_nothing_elevated(paths)
    pending = desk.status(RUN_ID)
    assert pending["disposition"] == PENDING_HUMAN and pending["missing"] == [SUBSTANCE]
    assert saved.read_bytes() == before


def test_signing_the_edited_files_does_not_clear_a_run_saved_with_the_original_hash(paths):
    run_dir = make_run(paths, escalated=False)
    save_current_assessment(paths, run_dir)
    desk, _ = desk_for(paths)
    make_nothing_elevated(paths)
    desk.sign_substance(CONTINUE, "Rijo John")
    assert desk.status(RUN_ID)["disposition"] == PENDING_HUMAN
    shutil.copy(ROOT / "substance.json", paths.substance_path)
    desk.sign_substance(CONTINUE, "Rijo John")
    assert desk.status(RUN_ID)["disposition"] == RELEASED


def test_saving_an_assessment_twice_fails_and_leaves_the_file_unchanged(paths):
    run_dir = make_run(paths, escalated=False)
    saved = save_current_assessment(paths, run_dir)
    before = saved.read_bytes()
    make_nothing_elevated(paths)
    with pytest.raises(FileExistsError):
        save_current_assessment(paths, run_dir)
    assert saved.read_bytes() == before


def test_disposition_points_at_the_assessment_file_only_when_it_exists(paths):
    run_dir = make_run(paths, escalated=False)
    desk, _ = desk_for(paths)
    assert "substance_assessment_file" not in desk.status(RUN_ID)
    assert not (run_dir / ASSESSMENT_FILE).exists()  # status never creates the file
    save_current_assessment(paths, run_dir)
    assert desk.status(RUN_ID)["substance_assessment_file"] == ASSESSMENT_FILE


def test_run_saves_its_assessment_once_before_the_first_call(tmp_path, paths):
    run_dir, _ = run_clean(tmp_path, paths)
    expected = load_assessment(paths.substance_path, paths.manifest_path)
    assert json.loads((run_dir / ASSESSMENT_FILE).read_text()) == expected.as_record()
    assert read_disposition(run_dir)["substance_assessment_file"] == ASSESSMENT_FILE
