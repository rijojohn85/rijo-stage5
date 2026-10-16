"""The coordinator loop: an LLM holding only launch_* tools drives the sub-agents.

The harness enforces structure and the LLM decides: each turn the model picks one launch, the
router runs it, and the model reads the validated result plus the current run_state. The run
ends only when a writer draft for a legal terminal state has a passing review of that exact
draft (finalization rechecks the state); a plain-text "done" before that is rejected.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from harness.launch_tools import launch_definitions, parse_launch
from harness.llm import LlmError
from harness.messages import assistant_message, tool_message
from harness.results import ErrorCode
from harness.run_log import NullRunLog

MAX_TURNS = 32
MAX_REFUSED_CLAIMS = 3   # refused completion claims tolerated before the run is declared a coordination failure
FAILED_PREFIX = "FAILED_"
FAILED_TURN_BUDGET = "FAILED_TURN_BUDGET"
FAILED_COORDINATOR_API = "FAILED_COORDINATOR_API"
FAILED_COORDINATION = "FAILED_COORDINATION"
COORDINATOR = "coordinator"


@dataclass(frozen=True)
class RunOutcome:
    result: str                 # VERIFIED / DEFERRED / HUMAN_PUNCH_OUT / FAILED_<reason>
    turns: int
    human_required: bool
    human_reasons: tuple
    cost_usd: float
    report_path: Path | None = None   # report.md: the accepted draft, or the deterministic failure report


class Coordinator:
    def __init__(self, *, llm, spec, system_prompt, router, run_state, publisher, terminal_check,
                 audit, prices, correlation_id, clock, log=None, max_turns=MAX_TURNS):
        self._llm, self._spec, self._system_prompt = llm, spec, system_prompt
        self._router, self._state, self._publisher = router, run_state, publisher
        self._terminal_check = terminal_check
        self._audit, self._prices = audit, prices
        self._correlation_id, self._clock = correlation_id, clock
        self._log, self._max_turns = log or NullRunLog(), max_turns
        self._tools = launch_definitions(spec.tools)
        self._attempts, self._seen = {}, {"human_required": False, "stale": ()}
        self._refused_claims = 0

    def run(self, report_handle):
        """`report_handle` is facts about the bug report (harness/report_handle.py), never its text."""
        messages = [{"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": json.dumps({"bug_report": report_handle}, indent=2)}]
        for turn in range(1, self._max_turns + 1):
            try:
                reply = self._llm.chat(self._spec.model, list(messages), self._tools)
            except LlmError as exc:
                self._log_turn(turn, self._audit_turn(turn, None, ok=False, error=(ErrorCode.API_ERROR, str(exc))))
                return self._outcome(FAILED_COORDINATOR_API, turn)
            self._log_turn(turn, self._audit_turn(turn, reply))
            messages.append(assistant_message(reply))
            messages.extend(self._claim_done(turn) if not reply.tool_calls else self._act(reply.tool_calls))
            accepted = self._accepted_state()
            if accepted:
                return self._outcome(accepted, turn)
            if self._refused_claims >= MAX_REFUSED_CLAIMS:
                return self._outcome(FAILED_COORDINATION, turn)
        return self._outcome(FAILED_TURN_BUDGET, self._max_turns)

    # --- one turn ------------------------------------------------------------------------------

    def _act(self, calls):
        """Execute only the first call; every extra call is answered with EXTRA_TOOL_CALL, unexecuted."""
        first, extras = calls[0], calls[1:]
        messages = [tool_message(first, self._launch(first))]
        for call in extras:
            refusal = {"ok": False, "error_code": str(ErrorCode.EXTRA_TOOL_CALL),
                       "detail": "only the first tool call of a turn is executed"}
            messages.append(tool_message(call, refusal))
        return messages

    def _launch(self, call):
        launch, error = parse_launch(call.name, call.arguments, self._spec.tools)
        if error:
            self._log.event("rejected", tool=call.name, error_code=error["error_code"], detail=error["detail"])
            return error
        self._log_launch(launch)
        result = self._router.execute(launch)
        self._log_result(launch, result)
        self._count_refused_claim(launch, result)
        self._log_state_changes()
        return {**result, "run_state": self._state.snapshot()}

    def _claim_done(self, turn):
        """The model answered without a tool call: only a finalized run may end this way."""
        detail = self._not_finished_detail()
        self._refused_claims += 1
        self._log.event("premature", turn=turn, detail=detail)
        return [{"role": "user", "content": f"Not finished: {detail} Continue with a launch_* tool call."}]

    def _count_refused_claim(self, launch, result):
        """A writer launch the terminal-state check refused is an illegal completion claim."""
        if launch.agent == "writer" and not result["ok"] and result["error_code"] == str(ErrorCode.PRECONDITION_MISSING):
            self._refused_claims += 1
            self._log.event("refused-claim", terminal_state=launch.terminal_state, count=self._refused_claims)

    # --- finalization --------------------------------------------------------------------------

    def _accepted_state(self):
        """The reviewed draft's state, if the evidence still supports it right now."""
        proposed = self._publisher.accepted_state()
        return proposed if proposed and self._terminal_check.run(proposed) is None else None

    def _not_finished_detail(self):
        proposed = self._publisher.accepted_state()
        if proposed is None:
            return ("there is no writer draft with a passing review of that exact draft "
                    "(or its headings no longer match the current evidence).")
        return f"the evidence for {proposed} no longer holds: {self._terminal_check.run(proposed).detail}."

    # --- audit and log -------------------------------------------------------------------------

    def _audit_turn(self, turn, reply, ok=True, error=None):
        """Append the turn's audit line and return it."""
        usage = reply.usage if reply else None
        first = reply.tool_calls[0].name if reply and reply.tool_calls else None
        entry = {
            "correlation_id": self._correlation_id, "step": None, "turn": turn, "agent": COORDINATOR,
            "target": None, "model": self._spec.model, "tool_call": first,
            "input_tokens": usage.input_tokens if usage else 0, "output_tokens": usage.output_tokens if usage else 0,
            "cost_usd": self._prices.cost_usd(self._spec.model, usage) if usage else 0.0,
            "ok": ok, "artifact": None, "sha256": None,
            "error_code": str(error[0]) if error else None, "detail": error[1] if error else None,
            "refused_tool_calls": max(0, len(reply.tool_calls) - 1) if reply else 0,
            "ts": self._clock().isoformat(),
        }
        self._audit.append(entry)
        return entry

    def _log_turn(self, turn, entry):
        """One line per coordinator turn with its calculated cost, so the log's costs add up to the total."""
        self._log.event("turn", n=f"{turn}/{self._max_turns}", tokens=f"{entry['input_tokens']}+{entry['output_tokens']}",
                        cost_usd=entry["cost_usd"], error_code=entry["error_code"])

    def _log_launch(self, launch):
        key = (launch.agent, launch.target)
        self._attempts[key] = self._attempts.get(key, 0) + 1
        self._log.event("launch", agent=launch.agent, target=launch.target, terminal_state=launch.terminal_state,
                        attempt=self._attempts[key], feedback="yes" if launch.feedback else None)

    def _log_result(self, launch, result):
        entry = self._audit.dispatches()[-1]
        tokens = f"{entry['input_tokens']}+{entry['output_tokens']}"
        if not result["ok"]:
            self._log.event("error", agent=launch.agent, error_code=result["error_code"], detail=result["detail"])
            if result["error_code"] == str(ErrorCode.REVISION_CAP_REACHED):
                self._log.event("cap-hit", agent=launch.agent)
            return
        self._log.event("ok", agent=launch.agent, artifact=entry["artifact"], tokens=tokens, cost_usd=entry["cost_usd"])
        if launch.agent == "reviewer":
            self._log.event("verdict", target=launch.target, verdict=result["artifact"]["verdict"],
                            findings=len(result["artifact"]["findings"]))

    def _log_state_changes(self):
        snapshot = self._state.snapshot()
        stale = tuple(a for a, s in snapshot["artifacts"].items() if s["exists"] and not s["fresh"])
        if stale != self._seen["stale"]:
            self._log.event("run_state", stale=",".join(stale) or "none")
        if snapshot["human_required"] != self._seen["human_required"]:
            self._log.event("run_state", human_required=snapshot["human_required"],
                            reasons="; ".join(snapshot["human_reasons"]))
        self._seen = {"human_required": snapshot["human_required"], "stale": stale}

    def _outcome(self, result, turns):
        path = self._publish(result)
        outcome = RunOutcome(result, turns, self._state.human_required(), tuple(self._state.human_reasons()),
                             self._audit.total_cost_usd(), path)
        self._log.event("terminal", state=result, turns=turns, human_required=outcome.human_required,
                        cost_usd=outcome.cost_usd, report=path.name)
        return outcome

    def _publish(self, result):
        """Every run ends with report.md: the reviewed draft when finalized, otherwise the failure report."""
        failed = result.startswith(FAILED_PREFIX)
        path, sha256 = self._publisher.publish_failure(result) if failed else self._publisher.publish_accepted()
        self._log.event("published", kind="failure-report" if failed else "accepted-draft", path=path, sha256=sha256)
        return path
