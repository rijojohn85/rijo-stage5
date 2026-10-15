"""The substance assessment: rating of every workflow, and whether it elevates the system.

Any `toy`, or two or more `peripheral`, means elevated: a human signs off once. The assessment
is bound to the exact bytes of substance.json and manifest.yaml, so editing either invalidates
an earlier sign-off.
"""
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

from guardrails import validate
from harness.guardrail_runner import run_guardrail
from harness.hashing import sha256_json, sha256_text
from harness.manifest import MANIFEST_PATH

SUBSTANCE_PATH = Path(__file__).resolve().parent.parent / "substance.json"
CORE, PERIPHERAL, TOY = "core", "peripheral", "toy"
PERIPHERAL_LIMIT = 2  # this many peripheral workflows (or any toy) elevates
SCHEMA_NAME = "substance"


class SubstanceError(Exception):
    """substance.json is invalid or does not describe exactly the manifest's workflows."""


@dataclass(frozen=True)
class Assessment:
    workflows: dict      # workflow -> {"rating", "why"}, verbatim from substance.json
    elevated: bool
    binding_sha256: str  # combined hash of substance.json and manifest.yaml

    def as_record(self):
        """The computed assessment, retained verbatim in a substance decision."""
        return {"workflows": self.workflows, "elevated": self.elevated, "binding_sha256": self.binding_sha256}


def is_elevated(workflows):
    ratings = [entry["rating"] for entry in workflows.values()]
    return TOY in ratings or ratings.count(PERIPHERAL) >= PERIPHERAL_LIMIT


def combined_hash(substance_text, manifest_text):
    return sha256_json({"substance.json": sha256_text(substance_text), "manifest.yaml": sha256_text(manifest_text)})


def manifest_workflows(manifest_text):
    return {entry["workflow"] for entry in yaml.safe_load(manifest_text)["agents"].values()}


def load_assessment(substance_path=SUBSTANCE_PATH, manifest_path=MANIFEST_PATH):
    substance_path, manifest_path = Path(substance_path), Path(manifest_path)
    result = run_guardrail(validate.main, [SCHEMA_NAME, str(substance_path)])
    if result.exit_code != 0:
        raise SubstanceError(f"{substance_path.name} is invalid: {result.stderr.strip()}")
    substance_text, manifest_text = (p.read_text(encoding="utf-8") for p in (substance_path, manifest_path))
    workflows = json.loads(substance_text)["workflows"]
    expected = manifest_workflows(manifest_text)
    if set(workflows) != expected:
        raise SubstanceError(f"{substance_path.name} workflows {sorted(workflows)} must equal the manifest's {sorted(expected)}")
    return Assessment(workflows, is_elevated(workflows), combined_hash(substance_text, manifest_text))
