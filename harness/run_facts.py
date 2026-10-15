"""What the human gate needs to know about one run directory, read-only."""
from dataclasses import dataclass
from pathlib import Path

from harness.hashing import sha256_text
from harness.report import result_of
from harness.report_publisher import REPORT_FILENAME
from harness.run_views import RunViews


@dataclass(frozen=True)
class RunFacts:
    run_id: str
    execution_result: str | None   # VERIFIED / DEFERRED / HUMAN_PUNCH_OUT / FAILED_<reason>; None while no report
    report_sha256: str | None      # None until the run has stopped (report.md is written once, at the end)
    report_text: str | None
    human_required: bool
    human_reasons: tuple

    @property
    def stopped(self):
        return self.report_sha256 is not None


def read_run_facts(run_dir, sandbox):
    run_dir = Path(run_dir)
    state = RunViews.build(run_dir, sandbox).state
    report_path = run_dir / REPORT_FILENAME
    text = report_path.read_text(encoding="utf-8") if report_path.exists() else None
    return RunFacts(run_dir.name, result_of(text) if text else None, sha256_text(text) if text is not None else None,
                    text, state.human_required(), tuple(state.human_reasons()))
