"""The read-only views over one run directory, built once so every part of a run shares them."""
from dataclasses import dataclass

from harness.audit import AuditLog
from harness.freshness import Freshness
from harness.report_builder import ReportBuilder
from harness.run_state import RunState
from harness.store import ArtifactStore
from harness.terminal_state import TerminalStateCheck


@dataclass(frozen=True)
class RunViews:
    store: ArtifactStore
    audit: AuditLog
    freshness: Freshness
    state: RunState
    terminal_check: TerminalStateCheck
    report: ReportBuilder

    @classmethod
    def build(cls, run_dir, sandbox):
        """Artifacts and audit.jsonl live under run_dir; freshness needs the sandbox for diff hashes."""
        store, audit = ArtifactStore(run_dir), AuditLog(run_dir / "audit.jsonl")
        freshness = Freshness(store, sandbox)
        state = RunState(store, audit, freshness)
        return cls(store, audit, freshness, state, TerminalStateCheck(store, freshness, state),
                   ReportBuilder(store, audit, state))
