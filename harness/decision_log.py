"""decisions/decisions.jsonl: the append-only log of human decisions.

Lines are only ever appended (file mode "a"); nothing here rewrites, truncates or deletes. A
decision never touches a run directory: it lives only in this log and the derived disposition.
"""
import json
from pathlib import Path

SUBSTANCE, PUNCH_OUT = "substance", "punch_out"
CONTINUE, REJECT = "continue", "reject"
KINDS = (SUBSTANCE, PUNCH_OUT)
DECISIONS = (CONTINUE, REJECT)


def substance_binding(assessment):
    """Binds to the combined substance.json + manifest.yaml hash and keeps the computed assessment verbatim."""
    return {"binding_sha256": assessment.binding_sha256, "assessment": assessment.as_record()}


def punch_out_binding(run_id, report_sha256):
    return {"run_id": run_id, "report_sha256": report_sha256}


def make_decision(*, timestamp, reviewer, kind, decision, note, binding):
    if kind not in KINDS or decision not in DECISIONS:
        raise ValueError(f"unknown kind {kind!r} or decision {decision!r}")
    return {"ts": timestamp, "reviewer": reviewer, "kind": kind, "decision": decision, "note": note, "binding": binding}


def binds_substance(assessment):
    return lambda binding: binding.get("binding_sha256") == assessment.binding_sha256


def binds_punch_out(run_id, report_sha256):
    return lambda binding: binding.get("run_id") == run_id and binding.get("report_sha256") == report_sha256


class DecisionLog:
    def __init__(self, path):
        self._path = Path(path)

    def append(self, decision):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(decision) + "\n")

    def entries(self):
        if not self._path.exists():
            return []
        return [json.loads(line) for line in self._path.read_text(encoding="utf-8").splitlines() if line]

    def latest_decision(self, kind, binds):
        """continue / reject / None: the newest line of this kind whose binding matches wins."""
        matching = [e["decision"] for e in self.entries() if e["kind"] == kind and binds(e["binding"])]
        return matching[-1] if matching else None
