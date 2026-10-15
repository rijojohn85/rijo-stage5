# Hand-run evidence

Two end-to-end runs driven by a human in the OpenCode TUI on 2026-10-15, using the
`opencode-go/deepseek-v4.1-flash` model. These are the runs that exercise the **skill
wrappers**, which the promptfoo suites cannot: the evals grade the prompts, not the
`SKILL.md` orchestration around them.

Raw artifacts for both runs are committed under `runs/`.

---

## Run 1 — `runs/20261015-044433` — a clear bug, fixed and verified

Report: `test-data/bugs/bug-clear.md` (`average([])` raises `ZeroDivisionError`).

```
/triage test-data/bugs/bug-clear.md   → bug / fix-now, gate clean      Next: /plan
/plan                                 → files: [calc.py], 2 steps     Next: /implement
/implement                            → changed: [calc.py]            Next: /fix-review
/fix-review                           → verdict: pass, 0 findings     Done: fix verified.
```

Every gate ran and passed, in order:

| Step | Check | Result |
|---|---|---|
| triage | `validate.py triage` | `OK` |
| triage | `escalate_gate.py --apply` | clean, no trigger matched (correct — not a data-loss report) |
| plan | `validate.py plan` | `OK` |
| implement | `validate.py implement` | `OK` |
| implement | `plan_scope.py --artifact` | `OK plan-scope: 1 changed path(s) within the plan` |
| implement | `git status --porcelain -- sandbox/` piped to `plan_scope.py --stdin` | `OK` — the working tree agreed with the report, so no unreported edit |
| fix-review | `validate.py review` | `OK` |

**The implementer proved red→green rather than asserting it.** It ran the plan's test
*before* editing and watched it fail:

```
ZeroDivisionError: division by zero
exit=1
Test fails as expected. Applying fix:
```

then re-ran it after the edit with `test_exit=0`. The review found the diff added only the
empty-input guard, with zero findings.

### Audit trail, from `runs/20261015-044433/audit.jsonl`

| step | input | output | step cost | cumulative | artifacts at that point |
|---|---|---|---|---|---|
| triage | 4,114 | 872 | $0.001658 | $0.001658 | `00-report.md`, `01-triage.json` |
| plan | 7,416 | 1,464 | $0.001351 | $0.003010 | + `02-plan.json` |
| implement | 10,313 | 2,332 | $0.001547 | $0.004557 | + `03-implement.json` |
| fix-review | 20,086 | 3,030 | $0.003231 | **$0.007787** | + `04-review.json` |

**A complete verified fix cost $0.0078**, measured per step, from a real run. That is the
number the certification asks for, and no single-agent eval could produce it.

---

## Run 2 — `runs/20261015-044837` — data loss, punched out to a human

Report: `test-data/bugs/escalation-data-loss.md` (`save_records` truncates before
serializing; customer records lost twice this week).

```
/triage test-data/bugs/escalation-data-loss.md  → bug / defer, escalation TRUE  Next: /escalate
/escalate                                        → brief presented, human chose
Done: human decision recorded.
```

Triage set `escalation: true` on its own, and the gate confirmed it (no override needed —
it only overrides when the agent said `false` and a trigger matched).

### The decision, as recorded in `runs/20261015-044837/05-escalation.json`

```json
{
  "reason": "Trigger: data loss — 01-triage.json sets escalation true because its rationale
             cites SPEC.md's requirement that \"An existing file at `path` is replaced only
             after the new content has been written successfully...\" and the report's
             statement that \"We have lost customer records twice this week in production.\"",
  "question": "For storage.py:save_records, which truncates the target via open(path, \"w\")
               before json.dumps runs, do you want this fixed in this workflow now, or left
               unchanged for a human owner to schedule?",
  "options": [
    "Fix now: change save_records to write to a temp file and replace path only after serialization succeeds.",
    "Leave the code unchanged and log the bug for a human owner (changes nothing today).",
    "Stop writes to the store until a fix ships."
  ],
  "risk_if_wrong": "...",
  "decision": "Leave the code unchanged",
  "decided_by": "human"
}
```

The brief has all four required properties: one decision, three concrete options, a **safe
default** (option 2 changes nothing today), and a specific risk. The agent did not
recommend and did not decide.

### The negative evidence, which is the point

The run directory contains:

```
00-report.md   01-triage.json   05-escalation.json   audit.jsonl
```

**No `02-plan.json`, no `03-implement.json`, no `04-review.json`.** The punch-out stopped the
workflow before any planning or editing, and the human's decision was "leave it unchanged",
so nothing was applied. `sandbox/` was verified clean afterwards. This is a decision the
agent was not permitted to make, and did not.

### Audit trail, `runs/20261015-044837/audit.jsonl`

| step | input | output | step cost | cumulative |
|---|---|---|---|---|
| triage | 7,593 | 1,277 | $0.002860 | $0.002860 |
| escalate | 10,816 | 2,345 | $0.001659 | $0.004519 |

A punched-out decision cost **$0.0045**.

---

## Run 3 — `runs/20261015-045418` — a feature dressed as a bug, deferred

Report: `test-data/bugs/feature-as-bug.md` ("add a `median(values)` function").

```
/triage test-data/bugs/feature-as-bug.md  → feature-as-bug / defer   Done: deferred.
```

```
Classification: feature-as-bug
Decision: defer
Escalation: false (validate OK, gate no trigger, audit OK)
```

The reasoning is the point, not the label. The agent did not say "no such function exists"; it
said SPEC.md establishes requirements for `calc.average` and `storage.save_records` only, so
**no written requirement establishes `median`** — a capability request, not a defect. It went
on to note that rule 4 defers feature-as-bug reports *even though the request is localized*,
which is exactly the trap this scenario is meant to catch: a request can look easy and still
not be the agent's to grant.

The escalation gate correctly did **not** fire, since nothing here is data loss, a security
exposure, or an outage. Left unrouted, deferring costs nothing; over-escalating would have
made the backstop noise.

Audit: triage 15,294 in / 1,330 out, **$0.003999** for the step.

### All three terminal states, by hand

The run directories are themselves the routing evidence — what a step wrote, and what it did
not:

| Run | Artifacts present | Terminal state |
|---|---|---|
| `20261015-044433` | `00`, `01`, **`02`, `03`, `04`** | `VERIFIED` — the whole chain ran |
| `20261015-044837` | `00`, `01`, **`05`** | `HUMAN_PUNCH_OUT` — stopped for a person |
| `20261015-045418` | `00`, `01` | `DEFERRED` — stopped at triage |

Total for the three hand-runs: $0.007787 + $0.004519 + $0.003999 = **$0.016305**, each figure
taken from that run's own `audit.jsonl`. `sandbox/` was verified clean after all three.

---

## Why this run is only possible with a person

The point of a punch-out is that the decision is not the agent's. Had this been automated,
a machine would have chosen whether to touch a system that had already destroyed customer
records. The escalation eval deliberately does **not** simulate a human for exactly this
reason — see the limitations in the top-level README.

---

## A usability defect found by this run

`/review` did **not** resolve to this project's skill. The transcript shows a generic
"You are a code reviewer. Your job is to review code changes..." prompt loading instead, and
the skill only ran after it was attached by hand as `@.opencode/skills/review/SKILL.md`.

Cause: a name collision. Other installed skills claim `/review` as a trigger. The skill has
been renamed to **`/fix-review`** (directory and frontmatter `name`), which resolves it.
Every reference in this repository was updated, and `prompts/review.md`,
`evals/promptfoo.review.yaml`, `schemas/review.json` and `04-review.json` keep their names
deliberately — only the user-facing command changed.

Found only by a real hand-run. No eval could have caught it, because evals never touch the
skill registry.
