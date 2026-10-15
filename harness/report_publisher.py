"""Finalization: decides when a writer draft is accepted and writes report.md exactly once.

report.md is a byte copy of the accepted draft, or, when no draft can be accepted, the
deterministic FAILED_<reason> report. Nothing here calls a model, and the accepted prose is
never regenerated or edited after its review.
"""
from pathlib import Path

from harness.hashing import sha256_text
from harness.report import result_of
from harness.store import WRITER

REPORT_FILENAME = "report.md"


class ReportPublisher:
    def __init__(self, run_dir, views):
        self._path = Path(run_dir) / REPORT_FILENAME
        self._views = views

    def accepted_state(self):
        """The terminal state of the latest draft, if that exact draft has a fresh passing review.

        The draft must also still equal a fresh deterministic render, so its headings, labels and
        banner match the evidence as it stands now (finalization recheck).
        """
        record = self._views.store.latest(WRITER)
        if record is None or not self._views.freshness.has_fresh_passing_review(WRITER):
            return None
        draft = self._views.store.draft_text(record)
        state = result_of(draft)
        return state if state and self._views.report.draft(state, record.artifact["sections"]) == draft else None

    def publish_accepted(self):
        """Copy the accepted draft to report.md (exclusive create). Returns (path, sha256)."""
        draft = self._views.store.draft_text(self._views.store.latest(WRITER))
        return self._write(draft)

    def publish_failure(self, result):
        return self._write(self._views.report.failure_report(result))

    def _write(self, text):
        with self._path.open("x", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return self._path, sha256_text(text)
