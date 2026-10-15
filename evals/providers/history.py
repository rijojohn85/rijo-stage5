"""Seeds a writer eval case: the run so far, as saved steps, exactly as a real run would have left them.

Each review is bound to whatever the latest artifact of its target was at that moment, so a
review of a replaced artifact is genuinely outdated. The writer then runs through the real
dispatcher, whose terminal-state check, run_state and report facts read these saved steps.
"""
from harness.agents import ARTIFACT_CHAIN
from harness.binding import expected_binding
from harness.store import REVIEWER


def seed_history(store, sandbox, history):
    for step, entry in enumerate(history, start=1):
        if "produce" in entry:
            _seed_artifact(store, step, entry["produce"], entry["artifact"])
        else:
            target = entry["review"]
            review = {"verdict": entry["verdict"], "findings": entry.get("findings", [])}
            store.save(step, REVIEWER, target, review, {}, expected_binding(target, store, sandbox))


def _seed_artifact(store, step, agent, artifact):
    inputs = {up: store.latest(up).sha256 for up in ARTIFACT_CHAIN[agent][:-1] if store.latest(up)}
    store.save(step, agent, None, artifact, inputs, {})
