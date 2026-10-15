"""Append-only audit.jsonl: one line for every dispatch, including rejected ones."""
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

    def next_step(self):
        """Step numbers (NN) increase on every dispatch, successful or not."""
        return len(self.entries()) + 1

    def failures(self):
        """Compact view of failed dispatches, for the escalation brief's context."""
        return [{"step": e["step"], "agent": e["agent"], "error_code": e["error_code"], "detail": e["detail"]}
                for e in self.entries() if not e["ok"]]
