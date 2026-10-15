# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Stage 5 certification build: a dispatch-only LLM coordinator that launches scoped sub-agents (triage, plan, implement, reviewer, escalate, writer) over the Stage 4 bug-fix workflow. It is a learning and certification project, not production software.

- **Authoritative plan:** `~/claude-plans/stage5-coordinator.md`. Build proceeds one numbered step at a time (Section 9), with a human review between steps. Do not start the next step until the user says so.
- **Build status:** steps 0 to 2 done (setup, `harness/tools.py`, `manifest.yaml`, LLM client and dispatcher: `harness/llm.py`, `harness/dispatcher.py` plus small single-purpose modules). Not built yet: coordinator, `run_state` and `human_required`, writer/report, decisions gate. `schemas/review.json` has optional binding echo fields (step 3 finishes the `target` change in `review.md`).
- **Stage 4 base** lives at `~/dev/rijo-stage4-2`. It is read-only reference. Copy from it, never edit it.

## Commands

Use the project venv (`.venv/`, git-ignored). Secrets are in `.env` (git-ignored, `export` lines), so load it with `set -a; source .env`. Never print `FIREWORKS_API_KEY`.

```bash
.venv/bin/python -m pytest -q tests                       # all tests
.venv/bin/python -m pytest -q tests/test_tools.py::<name> # one test
python3 guardrails/validate.py <schema-name> <artifact.json|->   # schema check (--list shows names)
.venv/bin/python scripts/smoke_tool_call.py               # tool-call smoke test, both models (needs .env)
promptfoo eval -c evals/promptfoo.<suite>.yaml --max-concurrency 1 --no-cache   # suites: triage plan implement review escalate workflow
```

The promptfoo providers in `evals/providers/` still call the opencode CLI (carried over from Stage 4). The plan repoints them to Fireworks in step 3.

## Architecture

The point of the design is separation of powers. Read the plan for details; these are the rules that span files:

- **`manifest.yaml` is the single source of truth for tool scoping.** Each agent lists its model and exact tools. `tests/test_manifest.py` fails on any extra tool. Coordinator tools are `launch_*` only, with no real tool. The reviewer has `read_file` only. Escalate and writer have none. Only implement may `edit_file` and `run_test`.
- **`harness/tools.py`:** `read_file`, `edit_file`, `run_test`, all confined to `sandbox/` (real-path resolution blocks `..` and symlinks). Every tool returns `{ok: true, ...}` or `{ok: false, error_code, detail}` and never raises.
- **Every sub-agent call is a fresh API call with empty history.** That is what makes the reviewer's context provably isolated.
- **The harness enforces structure and the LLM decides.** Deterministic checks sit at the delegation boundary: schema (`schemas/`, `guardrails/validate.py`), plan-scope (`guardrails/plan_scope.py`), escalation (`guardrails/escalate_gate.py` plus `escalate_rules.yaml`), and review binding. The coordinator receives a structured error, never malformed output.
- **Report headings and PASS/FAIL come from code, not the LLM** (`report.py`, planned).
- **Models:** coordinator `glm-5p3`, sub-agents `deepseek-v4p1-flash`, both via Fireworks with full `accounts/fireworks/models/...` ids. Never use `auto` routers. Prices are in `config/prices.json` and cost is calculated, not measured.
- **Runtime must not shell out to agentic CLIs** (claude, codex, opencode and similar), which is an automatic certification FAIL. Only code under `evals/` and `tests/` is exempt.

## Known accepted limits

- `run_test` accepts a free-form command and is not jailed. A model-chosen `python3 -c` can touch files outside `sandbox/`. The user chose to keep it simple. A bubblewrap jail (commit `ddac208`) was built and then reverted in `f4a5e11`. The backstops are the plan-scope check and `git status`.
- `evals/providers/opencode_cli.py` passes `--auto` and the full parent environment. It is eval-only and exempt. Revisit in step 7.

## Conventions

- **Code quality:** follow SOLID, DRY and clean-code principles. Small single-purpose modules and functions, no duplicated logic, intention-revealing names, dependencies injected so the LLM client can be faked in tests.
- **Git:** `.env` must stay untracked. Commit messages end with the Co-Authored-By and Claude-Session lines from the session attribution reminder.
