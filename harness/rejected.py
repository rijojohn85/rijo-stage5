"""Writes invalid model output under rejected/. It is evidence for humans, never returned to the coordinator."""
import json
from pathlib import Path

from harness.store import artifact_stem

REJECTED_DIRNAME = "rejected"


class RejectedWriter:
    def __init__(self, run_dir):
        self._dir = Path(run_dir) / REJECTED_DIRNAME

    def write(self, step, agent, target, failure):
        """Only dispatches that produced model output leave a file."""
        if failure.raw_output is None:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        body = {"error_code": str(failure.code), "detail": failure.detail, "raw_output": failure.raw_output}
        (self._dir / f"{artifact_stem(step, agent, target)}.json").write_text(
            json.dumps(body, indent=2) + "\n", encoding="utf-8")
