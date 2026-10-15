#!/usr/bin/env bash
# Run the coordinator governance suite and the workflow suite (paid Fireworks calls) and log everything.
# Usage: scripts/run_governance_evals.sh [coordinator|workflow ...]   (default: both)
# Tail a log from another terminal:  tail -f results/coordinator.log   or   tail -f results/workflow.log
# The workflow suite makes 12 live runs (4 cases x --repeat 3) and resets sandbox/ around each one.
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; source .env; set +a
export PROMPTFOO_PYTHON="$PWD/.venv/bin/python"

mkdir -p results

run_suite() {
  local suite=$1; shift
  local log="results/$suite.log"
  : > "$log"
  echo "=== $(date -Is) suite: $suite ===" | tee -a "$log"
  promptfoo eval -c "evals/promptfoo.$suite.yaml" --max-concurrency 1 --no-cache \
    -o "results/$suite.json" "$@" 2>&1 | tee -a "$log" || true  # a failing suite must not stop the next one
  echo "=== $(date -Is) done: $suite ===" | tee -a "$log"
}

SUITES=${*:-"coordinator workflow"}
for suite in $SUITES; do
  case "$suite" in
    workflow) run_suite workflow --repeat 3 ;;
    *) run_suite "$suite" ;;
  esac
done
