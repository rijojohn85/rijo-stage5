"""What the coordinator is shown of a successful launch: report quotes are removed first.

The report is untrusted text. The screen's artifact is reduced to a verdict and an evidence
count; escalate and writer output goes through the shared redaction (harness/redact.py, which
removes exact quotes only; see its limit). Triage and plan are shown unchanged: a residual risk.
Only the coordinator path uses this view; stored artifacts and the evals keep the full output.
"""
from harness.redact import redact_report_quotes

REDACTED_AGENTS = ("escalate", "writer")


class CoordinatorView:
    def __init__(self, report_text, store):
        self._report_text, self._store = report_text, store

    def result(self, agent, result):
        """The successful dispatch result with its artifact and summary as the coordinator may see them."""
        if agent == "screen":
            screen = result["artifact"]
            return {**result, "artifact": {"verdict": screen["verdict"], "evidence_items": len(screen["evidence"])}}
        if agent in REDACTED_AGENTS:
            return {**result, "summary": self._redact(result["summary"]), "artifact": self._redact(result["artifact"])}
        return result

    def _redact(self, value):
        return redact_report_quotes(value, self._report_text, exact_quotes=self._screen_evidence())

    def _screen_evidence(self):
        return [item for record in self._store.records("screen") for item in record.artifact["evidence"]]
