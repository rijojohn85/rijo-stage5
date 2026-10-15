#!/usr/bin/env bash
# Run the five helper eval suites (paid Fireworks calls) and log everything.
# Usage: scripts/run_helper_evals.sh [suite ...]   (default: all five)
# Tail the log from another terminal:  tail -f results/eval.log
set -euo pipefail

cd "$(dirname "$0")/.."
set -a; source .env; set +a
export PROMPTFOO_PYTHON="$PWD/.venv/bin/python"

mkdir -p results
LOG=results/eval.log
: > "$LOG"

run_suite() {
  local suite=$1; shift
  echo "=== $(date -Is) suite: $suite ===" | tee -a "$LOG"
  promptfoo eval -c "evals/promptfoo.$suite.yaml" --max-concurrency 1 --no-cache \
    -o "results/$suite.json" "$@" 2>&1 | tee -a "$LOG" || true  # a failing suite must not stop the rest
}

SUITES=${*:-"triage plan implement escalate review"}
for suite in $SUITES; do
  if [ "$suite" = review ]; then
    # Writer-draft cases are pending step 5, so skip them.
    run_suite review --filter-pattern '^(?!\[pending step 5\])'
  else
    run_suite "$suite"
  fi
done
echo "=== $(date -Is) done ===" | tee -a "$LOG"
