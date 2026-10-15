"""Everything one dispatch's preconditions and checks may look at."""
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DispatchContext:
    spec: Any  # manifest.AgentSpec
    target: str | None
    feedback: str | None
    store: Any  # store.ArtifactStore
    freshness: Any  # freshness.Freshness
    sandbox: Any  # GitSandbox-like: changed_files(), diff()
    audit: Any  # audit.AuditLog
    bug_report_path: Path
