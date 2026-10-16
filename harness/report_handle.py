"""The coordinator's stand-in for the bug report: facts about it, never its text."""
from pathlib import Path

from harness.hashing import sha256_text

NOTE = ("The report text is stored by the harness and given to the sub-agents. You do not see it; "
        "rely on the sub-agents' artifacts.")


def report_handle(path, correlation_id):
    text = Path(path).read_text(encoding="utf-8")
    return {"id": correlation_id, "length_chars": len(text), "sha256": sha256_text(text), "note": NOTE}
