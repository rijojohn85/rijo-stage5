"""What a review must be bound to: the exact target artifact (and diff, for implement)."""
from harness.hashing import sha256_text

DIFF_TARGET = "implement"  # the only target whose reviewed content includes a working-tree diff


def expected_binding(target, store, sandbox):
    """Fields the reviewer must echo back, or None when there is no target artifact."""
    record = store.latest(target)
    if record is None:
        return None
    binding = {"target": target, "reviewed_sha256": record.sha256}
    if target == DIFF_TARGET:
        binding["diff_sha256"] = sha256_text(sandbox.diff())
    return binding
