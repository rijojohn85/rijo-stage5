"""Append-only audit.jsonl: one line for every dispatch (including rejected ones) and coordinator turn.

Dispatch lines carry a step number (the NN of their artifact); coordinator turn lines have
step null, so turns never shift artifact numbering.
"""
import json
from pathlib import Path


class AuditLog:
    def __init__(self, path):
        self._path = Path(path)

    def append(self, entry):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def entries(self):
        if not self._path.exists():
            return []
        return [json.loads(line) for line in self._path.read_text(encoding="utf-8").splitlines() if line]

    def dispatches(self):
        """Entries for dispatches only (those with a step number), not coordinator turns."""
        return [e for e in self.entries() if e["step"] is not None]

    def next_step(self):
        """Step numbers (NN) increase on every dispatch, successful or not."""
        return len(self.dispatches()) + 1

    def failures(self):
        """Compact view of failed dispatches, for the escalation brief's context."""
        return [{"step": e["step"], "agent": e["agent"], "error_code": e["error_code"], "detail": e["detail"]}
                for e in self.dispatches() if not e["ok"]]

    def total_cost_usd(self):
        return round(sum(e["cost_usd"] for e in self.entries()), 6)
