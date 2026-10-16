"""A run's substance assessment, saved once beside its artifacts and never rewritten.

Editing substance.json or manifest.yaml later therefore cannot change what an earlier run was
assessed as. Runs made before this file existed have none and use the current files.
"""
import json
from pathlib import Path

from harness.substance import Assessment

ASSESSMENT_FILE = "substance-assessment.json"


def save_assessment(run_dir, assessment):
    """Exclusive create: a second save raises FileExistsError instead of overwriting."""
    with open(Path(run_dir) / ASSESSMENT_FILE, "x", encoding="utf-8") as handle:
        handle.write(json.dumps(assessment.as_record(), indent=2, sort_keys=True) + "\n")


def load_saved_assessment(run_dir):
    """The run's saved assessment, or None for a run that has none."""
    path = Path(run_dir) / ASSESSMENT_FILE
    if not path.exists():
        return None
    return Assessment.from_record(json.loads(path.read_text(encoding="utf-8")))
