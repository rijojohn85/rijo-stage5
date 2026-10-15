"""STUB for the writer and its review (build step 4). Step 5 replaces it with the real writer.

It makes no model call and writes no artifact. It exists so the coordinator loop can be
proved end to end: the proposed terminal state is genuinely validated (TerminalStateCheck),
the stub draft is hash-identified, and only a "review" of that exact draft counts as accepted.
Its audit lines carry stub=true and zero cost.
"""
from harness.hashing import sha256_json
from harness.results import ErrorCode, Failure, error_result, ok_result
from harness.store import REVIEWER

WRITER = "writer"
STUB_MODEL = "stub"


class WriterStub:
    def __init__(self, terminal_check, audit, correlation_id, clock):
        self._check, self._audit = terminal_check, audit
        self._correlation_id, self._clock = correlation_id, clock
        self._draft = None            # {"terminal_state", "feedback", "draft_sha256"}
        self._reviewed_sha = None

    def write(self, terminal_state, feedback=None):
        failure = self._check.run(terminal_state)
        if failure:
            return self._finish(WRITER, None, failure)
        body = {"terminal_state": terminal_state, "feedback": feedback}
        self._draft = {**body, "draft_sha256": sha256_json(body)}
        self._reviewed_sha = None
        return self._finish(WRITER, None, None, f"stub draft for {terminal_state}", self._draft)

    def review(self):
        if self._draft is None:
            return self._finish(REVIEWER, WRITER,
                                Failure(ErrorCode.PRECONDITION_MISSING, "no writer draft to review"))
        self._reviewed_sha = self._draft["draft_sha256"]
        artifact = {"verdict": "pass", "findings": [], "target": WRITER, "draft_sha256": self._reviewed_sha}
        return self._finish(REVIEWER, WRITER, None, "pass, 0 finding(s) (stub)", artifact)

    def accepted_state(self):
        """The terminal state whose exact draft has a passing review, else None."""
        draft = self._draft
        if draft is not None and self._reviewed_sha == draft["draft_sha256"]:
            return draft["terminal_state"]
        return None

    def _finish(self, agent, target, failure, summary="", artifact=None):
        self._audit.append({
            "correlation_id": self._correlation_id, "step": self._audit.next_step(), "agent": agent,
            "target": target, "model": STUB_MODEL, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
            "ok": failure is None, "artifact": None, "sha256": artifact and sha256_json(artifact),
            "error_code": str(failure.code) if failure else None,
            "detail": failure.detail if failure else None, "refused_tool_calls": 0,
            "stub": True, "ts": self._clock().isoformat(),
        })
        return error_result(agent, failure) if failure else ok_result(agent, summary, artifact)
