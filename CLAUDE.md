# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Stage 5 certification build: a dispatch-only LLM coordinator that launches scoped sub-agents (triage, plan, implement, reviewer, escalate, writer) over the Stage 4 bug-fix workflow. It is a learning and certification project, not production software.

- **Authoritative plan:** `~/claude-plans/stage5-coordinator.md`. Build proceeds one numbered step at a time (Section 9), with a human review between steps. Do not start the next step until the user says so.
- **Build status:** steps 0 to 5 done. Steps 0 to 2: setup, `harness/tools.py`, `manifest.yaml`, LLM client and dispatcher (`harness/llm.py`, `harness/dispatcher.py` plus small single-purpose modules). Step 3: reviewer `target`, optional `reviewer_feedback` and `previous_artifact` in the producer prompts, `evals/providers/fireworks.py`, named criteria in `evals/criteria.py`, revision cases, helper-suite `results/<suite>.json`. Step 4: coordinator (`prompts/coordinator.md`, `harness/coordinator.py`, `coordinator_wiring.py`, `launch_tools.py`, `launch_router.py`, `run_state.py` for staleness and `human_required`, `terminal_state.py`, `run_log.py`); entry `python -m harness.run <bug.md>` writes `runs/<id>/` with `audit.jsonl` and `run.log` (follow it with `tail -f runs/<id>/run.log`; each `turn` line carries its calculated cost). Step 5: real writer (`prompts/writer.md`, `schemas/writer.json`, `harness/writer_checks.py`, `writer_inputs.py`, `report.py` for deterministic labels and headings, `report_builder.py`, `report_publisher.py`, `run_views.py`); the writer is dispatched like any agent, each attempt saves `NN-writer.json` plus `NN-writer-draft.md`, the reviewer is bound to the draft hash, and every run ends with `report.md` (a byte copy of the reviewed draft, or the deterministic `FAILED_<reason>` report written by `Coordinator._outcome`). `evals/promptfoo.writer.yaml` (criteria `structure-valid`, `no-new-findings`, `tone`, `headings-untouched`; the last two LLM-judged by `evals/judge.py` with the rubrics in `evals/rubrics/`) and the two reviewer writer-draft cases are live, with results in `results/writer.json` and `results/review.json`. Not built yet: decisions gate (`substance.json`, `harness/decisions.py`, `disposition.json`) and the workflow eval (`promptfoo.workflow.yaml` and `providers/workflow.py` still use the removed CLI provider; step 7 replaces them).
- **Stage 4 base** lives at `~/dev/rijo-stage4-2`. It is read-only reference. Copy from it, never edit it.

## Commands

Use the project venv (`.venv/`, git-ignored). Secrets are in `.env` (git-ignored, `export` lines), so load it with `set -a; source .env`. Never print `FIREWORKS_API_KEY`.

```bash
.venv/bin/python -m pytest -q tests                       # all tests
.venv/bin/python -m pytest -q tests/test_tools.py::<name> # one test
python3 guardrails/validate.py <schema-name> <artifact.json|->   # schema check (--list shows names)
.venv/bin/python scripts/smoke_tool_call.py               # tool-call smoke test, both models (needs .env)
PROMPTFOO_PYTHON=$PWD/.venv/bin/python promptfoo eval -c evals/promptfoo.<suite>.yaml --max-concurrency 1 --no-cache -o results/<suite>.json   # suites: triage plan implement review escalate writer (workflow is step 7)
```

The promptfoo helper suites use `evals/providers/fireworks.py`, which runs the real dispatcher (`harness/wiring.py`) with the Fireworks client and makes paid calls (load `.env` first; `PROMPTFOO_PYTHON` must point at the venv or the worker never starts). Each assertion carries a `metric:` label implemented in `evals/criteria.py`. Pytest never calls the network: `tests/test_eval_plumbing.py` drives the provider with a fake LLM.

## Architecture

The point of the design is separation of powers. Read the plan for details; these are the rules that span files:

- **`manifest.yaml` is the single source of truth for tool scoping.** Each agent lists its model and exact tools. `tests/test_manifest.py` fails on any extra tool. Coordinator tools are `launch_*` only, with no real tool. The reviewer has `read_file` only. Escalate and writer have none. Only implement may `edit_file` and `run_test`.
- **`harness/tools.py`:** `read_file`, `edit_file`, `run_test`, all confined to `sandbox/` (real-path resolution blocks `..` and symlinks). Every tool returns `{ok: true, ...}` or `{ok: false, error_code, detail}` and never raises.
- **Every sub-agent call is a fresh API call with empty history.** That is what makes the reviewer's context provably isolated.
- **The harness enforces structure and the LLM decides.** Deterministic checks sit at the delegation boundary: schema (`schemas/`, `guardrails/validate.py`), plan-scope (`guardrails/plan_scope.py`), escalation (`guardrails/escalate_gate.py` plus `escalate_rules.yaml`), and review binding. The coordinator receives a structured error, never malformed output.
- **Report headings and PASS/FAIL come from code, not the LLM** (`harness/report.py`).
- **Models:** coordinator `glm-5p3`, sub-agents `deepseek-v4p1-flash`, both via Fireworks with full `accounts/fireworks/models/...` ids. Never use `auto` routers. Prices are in `config/prices.json` and cost is calculated, not measured.
- **Runtime must not shell out to agentic CLIs** (claude, codex, opencode and similar), which is an automatic certification FAIL. Only code under `evals/` and `tests/` is exempt.

## Known accepted limits

- `run_test` accepts a free-form command and is not jailed. A model-chosen `python3 -c` can touch files outside `sandbox/`. The user chose to keep it simple. A bubblewrap jail (commit `ddac208`) was built and then reverted in `f4a5e11`. The backstops are the plan-scope check and `git status`.
- `evals/providers/workflow.py` still imports the removed `opencode_cli.py` and is broken until step 7 replaces it. The helper suites no longer use any CLI.

## Conventions

- **Code quality:** follow SOLID, DRY and clean-code principles. Small single-purpose modules and functions, no duplicated logic, intention-revealing names, dependencies injected so the LLM client can be faked in tests.
- **Git:** `.env` must stay untracked. Commit messages end with the Co-Authored-By and Claude-Session lines from the session attribution reminder.
