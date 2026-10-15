"""Run directory artifacts: NN-<agent>[-<target>].json plus a .meta.json sidecar.

The artifact file holds exactly the validated model output (so guardrails/validate.py can
check it directly). Hashes that bind it to its inputs live in the sidecar.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from harness.hashing import sha256_json

REVIEWER = "reviewer"
WRITER = "writer"
DRAFT_SUFFIX = "-draft.md"  # NN-writer-draft.md sits next to NN-writer.json


def artifact_stem(step, agent, target=None):
    suffix = f"-{target}" if target else ""
    return f"{step:02d}-{agent}{suffix}"


def draft_filename(record):
    return record.filename.removesuffix(".json") + DRAFT_SUFFIX


@dataclass(frozen=True)
class Record:
    step: int
    agent: str
    target: str | None
    artifact: dict
    sha256: str
    inputs_sha256: dict = field(default_factory=dict)
    binding: dict = field(default_factory=dict)
    filename: str = ""


class ArtifactStore:
    def __init__(self, run_dir):
        self._dir = Path(run_dir)

    def save(self, step, agent, target, artifact, inputs_sha256, binding):
        stem = artifact_stem(step, agent, target)
        record = Record(step, agent, target, artifact, sha256_json(artifact), inputs_sha256, binding, f"{stem}.json")
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / record.filename).write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
        meta = {"step": step, "agent": agent, "target": target, "sha256": record.sha256,
                "inputs_sha256": inputs_sha256, "binding": binding}
        (self._dir / f"{stem}.meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return record

    def records(self, agent=None, target=None):
        """Saved records in step order, optionally filtered by agent and review target."""
        found = [self._load(p) for p in sorted(self._dir.glob("*.meta.json"))]
        return [r for r in found if (agent is None or r.agent == agent) and (target is None or r.target == target)]

    def latest(self, agent, target=None):
        matching = self.records(agent, target)
        return matching[-1] if matching else None

    def path_of(self, record):
        return self._dir / record.filename

    def write_draft(self, record, text):
        """A writer attempt's rendered markdown. Exclusive create: a draft is never overwritten."""
        with self._draft_path(record).open("x", encoding="utf-8") as handle:
            handle.write(text)

    def draft_text(self, record):
        return self._draft_path(record).read_text(encoding="utf-8")

    def _draft_path(self, record):
        return self._dir / draft_filename(record)

    def _load(self, meta_path):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        filename = meta_path.name.removesuffix(".meta.json") + ".json"
        artifact = json.loads((self._dir / filename).read_text(encoding="utf-8"))
        return Record(meta["step"], meta["agent"], meta["target"], artifact, meta["sha256"],
                      meta["inputs_sha256"], meta["binding"], filename)
