"""Tiny JSON record store used as the sandbox for the workflow.

Requirements live in SPEC.md.
"""

import json


def save_records(path, records):
    """Write records to path as JSON."""
    with open(path, "w") as handle:
        handle.write(json.dumps(records, indent=2))


def load_records(path):
    """Read records from path."""
    with open(path) as handle:
        return json.load(handle)
