"""Human disposition of a run: computed from the decision log, never replacing the execution result.

Rules, in order: REJECTED (a required decision is reject) > PENDING_HUMAN (a required decision
is missing) > RELEASED (something was required and all of it is continue) > FINAL (nothing was
required). Both gates must be satisfied when both apply; the latest decision per binding counts.
"""
import json
from pathlib import Path

from harness.assessment_file import ASSESSMENT_FILE, load_saved_assessment
from harness.decision_log import PUNCH_OUT, REJECT, SUBSTANCE, binds_punch_out, binds_substance
from harness.run_facts import read_run_facts

REJECTED, PENDING_HUMAN, RELEASED, FINAL = "REJECTED", "PENDING_HUMAN", "RELEASED", "FINAL"
DISPOSITION_FILENAME = "disposition.json"


def compute_disposition(required):
    """required: {kind: latest decision or None} for every gate that applies. Returns (disposition, missing)."""
    missing = [kind for kind, decision in required.items() if decision is None]
    if REJECT in required.values():
        return REJECTED, missing
    if missing:
        return PENDING_HUMAN, missing
    return (RELEASED if required else FINAL), missing


def required_gates(log, assessment, facts):
    """{kind: latest decision} for each gate that applies to this run."""
    gates = {}
    if assessment.elevated:
        gates[SUBSTANCE] = log.latest_decision(SUBSTANCE, binds_substance(assessment))
    if facts.human_required:
        decision = log.latest_decision(PUNCH_OUT, binds_punch_out(facts.run_id, facts.report_sha256)) if facts.stopped else None
        gates[PUNCH_OUT] = decision
    return gates


def build_disposition(log, assessment, facts):
    gates = required_gates(log, assessment, facts)
    disposition, missing = compute_disposition(gates)
    return {
        "run_id": facts.run_id,
        "execution_result": facts.execution_result,
        "disposition": disposition,
        "missing": missing,
        "required": sorted(gates),
        "decisions": gates,
        "human_required": facts.human_required,
        "human_reasons": list(facts.human_reasons),
        "report_sha256": facts.report_sha256,
        "substance_elevated": assessment.elevated,
        "substance_binding_sha256": assessment.binding_sha256,
    }


def write_disposition(run_dir, disposition):
    """A derived view: rewritten on every refresh. report.md and the numbered artifacts are never touched."""
    path = Path(run_dir) / DISPOSITION_FILENAME
    path.write_text(json.dumps(disposition, indent=2) + "\n", encoding="utf-8")
    return path


def describe(disposition):
    """The one-line display: disposition beside, never replacing, the execution result."""
    text = f"execution result: {disposition['execution_result']} | human disposition: {disposition['disposition']}"
    return text + (f" (missing: {', '.join(disposition['missing'])})" if disposition["missing"] else "")


def refresh_disposition(run_dir, sandbox, log, assessment):
    """Recompute from the log and the run as it stands, write disposition.json, return the disposition.

    The run's assessment is read from its write-once substance-assessment.json when present;
    `assessment` (the current files) is used only for runs that have none.
    """
    saved = load_saved_assessment(run_dir)
    disposition = build_disposition(log, saved or assessment, read_run_facts(run_dir, sandbox))
    if saved:
        disposition["substance_assessment_file"] = ASSESSMENT_FILE
    write_disposition(run_dir, disposition)
    return disposition


def read_disposition(run_dir):
    return json.loads((Path(run_dir) / DISPOSITION_FILENAME).read_text(encoding="utf-8"))
