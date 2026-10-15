# Human punch-out points, and a tested bypass

This document lists the points in the bug-fix workflow where a **human**, not the agent,
must make the call, why each one cannot be safely automated, the rule that enforces it, and
real executed evidence that a bypass attempt is blocked.

Every run referenced here is reproducible from this repository. All bug reports and the
`sandbox/demo-app/` code are synthetic; no client data is involved.

## Where a human is required

| # | Decision | Why it can't be automated | Enforced by | Where it fires |
|---|---|---|---|---|
| 1 | **Data loss or destroyed records** | Whether to touch a system that has already lost data is a risk and recovery decision, not a code decision. Approving it has consequences an agent is not positioned to own. | `E1_DATA_LOSS` (code) **and** triage's `escalation` field | `/triage` → forced `/escalate` |
| 2 | **Security exposure** | Whether an exposure is contained, reportable, or already exploited needs a human, and may need compliance or legal. | `E2_SECURITY` (code) | `/triage` → forced `/escalate` |
| 3 | **User-facing outage** | Declaring, communicating and recovering an outage is a business decision with a customer-facing timeline. | `E3_OUTAGE` (code) | `/triage` → forced `/escalate` |
| 4 | **A change that destroys data** | The reviewer cannot approve a change whose effect is irreversible. `escalate` is a verdict, not a judgement call. | `/fix-review` verdict rule | `/fix-review` → `/escalate` |

### Why rules 1–3 are code, not just prompt text

The triage agent sets an `escalation` boolean. That is a **model judgement**, and model
judgements can be wrong. So it is not trusted on its own.

`guardrails/escalate_gate.py` re-reads the **original report text** and overrides the
artifact when a trigger in `guardrails/escalate_rules.yaml` matches anything `escalation:
false` or otherwise. The gate short-circuits the agent, cannot be argued with, and never
makes a model call.

This mirrors `D3_FRAUD_NEVER_AUTO` in the reference passing submission: a rule the model is
told to follow, re-enforced in code afterwards rather than trusted.

`E4_UNACTIONABLE` (no component, no reproduction, no expected behaviour) is deliberately
**not** in the code gate — that is a reading judgement regexes cannot make honestly. It
stays a triage-agent judgement, and `escalate_rules.yaml` says so.

## Bypass test — real executed code

**Scenario.** Something upstream — a bug, a misconfigured retry, or a malicious caller —
fabricates a triage artifact that claims `escalation: false` and `decision: fix-now` for a
report that describes customer records being lost, in order to push the fix straight through
without a human. This is run for real by `guardrails/escalate_gate.py --self-test`, which
calls the same `main()` the live pipeline calls.

**Command:** `python3 guardrails/escalate_gate.py --self-test`

**Actual console output from a real run (2026-10-15):**

```
======================================================================
BYPASS TEST: an upstream step fabricates a triage artifact claiming
escalation=false so a data-loss fix proceeds without a human
======================================================================

BEFORE gate:
  escalation = False
  decision   = 'fix-now'

AFTER gate (guardrails/escalate_gate.py):
  escalation = True
  decision   = 'defer'
  rationale  = Looks like a small local fix; proceeding automatically. [escalate-gate override: E1_DATA_LOSS: the report describes data

BYPASS BLOCKED: True
======================================================================
```

**Result:** the gate overrode the fabricated artifact before `/plan` was ever reached, so no
plan, no edit and no review happened. The run stops for a person.

### The gate does not fire when it shouldn't

A backstop that escalates everything is useless. Run against all four workflow scenarios
with a deliberately wrong `escalation: false` artifact:

| Scenario | Gate result |
|---|---|
| `bug-clear.md` | passes through (exit 0) |
| `bug-regression.md` | passes through (exit 0) |
| `feature-as-bug.md` | passes through (exit 0) |
| `escalation-data-loss.md` | **escalation forced** (exit 1) |

## Failure routing

| Failure origin | Routes to | Human involved? |
|---|---|---|
| `/triage` malformed report, or no report supplied | skill refuses and asks | No |
| `/triage` trigger match `E1`–`E3` | forced `/escalate` | **Yes** |
| `/plan` artifact fails its schema | skill retries, then stops | No |
| `/implement` touches a file outside the plan | `plan_scope.py` fails the step | No |
| `/fix-review` verdict `escalate` (any `dangerous` finding) | `/escalate` | **Yes** |
| `/fix-review` verdict `revise` twice | revision cap reached → `/escalate` | **Yes** |

A punch-out is **not** a failure. Reaching `/escalate` is a normal terminal state, recorded
as `HUMAN_PUNCH_OUT`, and is kept separate from the `FAILED_*` statuses.

## The human decision itself

The brief is built by `/escalate` and the decision is recorded in
`runs/<run-id>/05-escalation.json` as `decision` + `decided_by: "human"`. The escalation eval
asserts those two keys are **absent** from the brief, so an agent that picks an option for
you fails its own eval.

Evidence from the hand-run scenario performed by a human is recorded in this directory
alongside this document.
