"""A clean, passing-reviewed screen saved as step 0, for runs that start at triage.

Triage is refused unless the bug report was screened clean and that screen passed review. Eval
cases and tests that exercise triage or later agents seed it directly in the store, so step
numbers 1..n and the audit log are unchanged.
"""
from harness.binding import expected_binding
from harness.store import REVIEWER

PASSING_REVIEW = {"verdict": "pass", "findings": []}
CLEAN_SCREEN = {"verdict": "clean", "evidence": [], "reason": "eval seed"}
SEED_STEP = 0


def seed_clean_screen(store, sandbox):
    """Save the clean screen and a passing review bound to it."""
    store.save(SEED_STEP, "screen", None, CLEAN_SCREEN, {}, {})
    store.save(SEED_STEP, REVIEWER, "screen", PASSING_REVIEW, {}, expected_binding("screen", store, sandbox))
