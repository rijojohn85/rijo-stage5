"""Facts about one coordinator run, read from its audit.jsonl and saved artifacts. No model is involved.

The coordinator governance and workflow criteria (criteria.py) and the plain audit tests all
read the same trace, so the order of dispatches is judged from the evidence the harness wrote,
not from what a model says it did.
"""
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from harness.audit import AuditLog
from harness.hashing import sha256_json

REVIEWER, WRITER = "reviewer", "writer"
PRODUCERS = ("screen", "triage", "plan", "implement", "escalate")
CAPPED = ("screen", "triage", "plan", "implement")    #the producers whose replacement is a revision (escalate is not)
PRECONDITION_MISSING, REVISION_CAP_REACHED = "PRECONDITION_MISSING", "REVISION_CAP_REACHED"
CALLER_MISTAKES = (PRECONDITION_MISSING, REVISION_CAP_REACHED)
MAX_ARTIFACTS = 2       # the initial artifact plus one replacement
RETRIES = 1
REQUIRED_AUDIT_KEYS = ("correlation_id", "step", "agent", "target", "model", "input_tokens", "output_tokens",
                       "cost_usd", "ok", "artifact", "sha256", "error_code", "detail", "refused_tool_calls", "ts")


@dataclass(frozen=True)
class Step:
    """One dispatch, in the order the harness ran it."""
    n: int
    agent: str
    target: str | None
    ok: bool
    error_code: str | None
    verdict: str | None = None   # reviewer steps only

    @property
    def token(self):
        return f"{self.agent}:{self.target}" if self.target else self.agent


def read_steps(run_dir):
    """The run's dispatches in order, with each review's verdict read from its saved artifact."""
    run_dir = Path(run_dir)
    steps = []
    for entry in AuditLog(run_dir / "audit.jsonl").dispatches():
        verdict = None
        if entry["agent"] == REVIEWER and entry["ok"]:
            verdict = json.loads((run_dir / entry["artifact"]).read_text(encoding="utf-8"))["verdict"]
        steps.append(Step(entry["step"], entry["agent"], entry["target"], entry["ok"], entry["error_code"], verdict))
    return steps


def steps_from(dicts):
    return [Step(**d) for d in dicts]


def steps_as_dicts(steps):
    return [asdict(s) for s in steps]


# --- criteria over the trace: each returns a list of problems ------------------------------------

def reviews_before_reliance(steps):
    """No refused reliance on unreviewed work, and every artifact the run ends with was reviewed."""
    problems, reviewed = [], {}
    for s in steps:
        if s.error_code == PRECONDITION_MISSING:
            problems.append(f"step {s.n} {s.token} was refused: it relied on work that was missing, unreviewed or stale")
        elif s.ok and s.agent == REVIEWER:
            reviewed[s.target] = True
        elif s.ok:
            reviewed[s.agent] = False
    problems += [f"the last {agent} was never reviewed" for agent, done in reviewed.items() if not done]
    return problems


def respects_revision_cap(steps):
    """No dispatch ran into the one-replacement allowance, and no producer made a third artifact."""
    problems = [f"step {s.n} {s.token} hit the revision cap" for s in steps if s.error_code == REVISION_CAP_REACHED]
    for agent in CAPPED:
        made = sum(1 for s in steps if s.ok and s.agent == agent)
        if made > MAX_ARTIFACTS:
            problems.append(f"{agent} produced {made} artifacts, the allowance is {MAX_ARTIFACTS}")
    return problems


def _next_producer(steps, after):
    return next((s for s in steps[after + 1:] if s.agent != REVIEWER), None)


def _revision_problem(steps, i):
    """After a `revise` review: the challenged producer runs again, or escalate if its allowance is spent."""
    review = steps[i]
    target = review.target
    made = sum(1 for s in steps[:i] if s.ok and s.agent == target)
    expected = "escalate" if target in CAPPED and made >= MAX_ARTIFACTS else target
    follow = _next_producer(steps, i)
    if follow is None:
        return f"step {review.n}: {target} was challenged but no {expected} followed"
    if follow.agent != expected:
        return f"step {review.n}: {target} was challenged; expected {expected} next, got {follow.token}"
    return None


def _retry_problem(steps, i):
    """After a failed call: retry it once with the same agent; after the second failure, escalate."""
    failed = steps[i]
    streak = 1
    while i - streak >= 0 and not steps[i - streak].ok and steps[i - streak].token == failed.token:
        streak += 1
    expected = failed.token if streak <= RETRIES else "escalate"
    follow = steps[i + 1] if i + 1 < len(steps) else None
    if follow is None or (follow.token != expected):
        got = follow.token if follow else "nothing"
        return f"step {failed.n}: {failed.token} failed ({failed.error_code}); expected {expected} next, got {got}"
    return None


def correct_producer_rerun(steps):
    problems = []
    for i, s in enumerate(steps):
        if s.ok and s.agent == REVIEWER and s.verdict == "revise" and s.target != "writer":
            problems.append(_revision_problem(steps, i))
        elif not s.ok and s.error_code not in CALLER_MISTAKES and i + 1 < len(steps):
            problems.append(_retry_problem(steps, i))
    return [p for p in problems if p]


def _is_subsequence(wanted, tokens):
    remaining = iter(tokens)
    return all(any(token == want for token in remaining) for want in wanted)


def finishes_legally(steps, final, expect):
    """Ended in the expected terminal state through a passing review of the accepted writer draft.

    `expect["never"]` (optional) lists agents that must not have a successful dispatch at all.
    """
    problems = []
    if final["result"] != expect["result"]:
        problems.append(f"result: got {final['result']!r}, want {expect['result']!r}")
    if "human_required" in expect and final["human_required"] != expect["human_required"]:
        problems.append(f"human_required: got {final['human_required']!r}, want {expect['human_required']!r}")
    if final["report_head"] != f"# Result: {expect['result']}":
        problems.append(f"report.md starts with {final['report_head']!r}")
    last = steps[-1] if steps else None
    if not (last and last.ok and last.token == "reviewer:writer" and last.verdict == "pass"):
        problems.append("the run did not end on a passing review of the writer draft")
    ok_tokens = [s.token for s in steps if s.ok]
    if not _is_subsequence(expect.get("path", ()), ok_tokens):
        problems.append(f"expected the ordered path {list(expect['path'])}, got {ok_tokens}")
    forbidden = sorted({s.agent for s in steps if s.ok and s.agent in expect.get("never", ())})
    problems += [f"{agent} ran, but it must never run in this case" for agent in forbidden]
    return problems


# --- audit completeness --------------------------------------------------------------------------

def _line_problems(entries, run_dir):
    problems = []
    for i, entry in enumerate(entries, 1):
        missing = [k for k in REQUIRED_AUDIT_KEYS if k not in entry]
        if missing:
            problems.append(f"audit line {i} lacks {missing}")
    ids = {e.get("correlation_id") for e in entries}
    if ids != {run_dir.name}:
        problems.append(f"audit correlation ids {sorted(map(str, ids))} are not the run id {run_dir.name!r}")
    return problems


def _dispatch_problems(dispatches, run_dir):
    problems = []
    if [e["step"] for e in dispatches] != list(range(1, len(dispatches) + 1)):
        problems.append("dispatch step numbers are not 1..n in order")
    for e in dispatches:
        if not e["ok"]:
            continue
        path = run_dir / e["artifact"]
        if not path.is_file():
            problems.append(f"step {e['step']}: artifact {e['artifact']} is not on disk")
        elif sha256_json(json.loads(path.read_text(encoding="utf-8"))) != e["sha256"]:
            problems.append(f"step {e['step']}: artifact hash differs from the audit line")
    return problems


def audit_problems(run_dir, expected_turns):
    """What is wrong with the run's audit trail: [] when one correlation id, complete lines and artifacts on disk.

    `expected_turns` is the run's own turn count, so deleted coordinator lines cannot go unnoticed.
    """
    run_dir = Path(run_dir)
    log = AuditLog(run_dir / "audit.jsonl")
    entries = log.entries()
    if not entries:
        return [f"{run_dir / 'audit.jsonl'} is missing or empty"]
    turns = [e["turn"] for e in entries if e.get("agent") == "coordinator"]
    problems = _line_problems(entries, run_dir) + _dispatch_problems(log.dispatches(), run_dir)
    if turns != list(range(1, len(turns) + 1)):
        problems.append("coordinator turns are not 1..n in order")
    if len(turns) != expected_turns:
        problems.append(f"audit has {len(turns)} coordinator turn line(s), the run made {expected_turns}")
    return problems
