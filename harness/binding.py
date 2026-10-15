"""What a review must be bound to: the exact target artifact (and diff, for implement)."""
from harness.hashing import sha256_text
from harness.store import WRITER

DIFF_TARGET = "implement"  # the target whose reviewed content includes a working-tree diff
DRAFT_TARGET = WRITER      # the target whose reviewed content is a rendered markdown draft


def expected_binding(target, store, sandbox):
    """Fields the reviewer must echo back, or None when there is no target artifact."""
    record = store.latest(target)
    if record is None:
        return None
    binding = {"target": target, "reviewed_sha256": record.sha256}
    if target == DIFF_TARGET:
        binding["diff_sha256"] = sha256_text(sandbox.diff())
    if target == DRAFT_TARGET:
        binding["draft_sha256"] = sha256_text(store.draft_text(record))
    return binding
