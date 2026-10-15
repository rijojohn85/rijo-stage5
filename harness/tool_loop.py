"""The bounded tool-use conversation for one helper dispatch.

Every call starts from an empty history (system + user only), which is what makes a
helper's context isolated by construction.
"""
import json
from dataclasses import dataclass

from harness.llm import LlmError, Usage
from harness.results import ErrorCode, Failure

MAX_TURNS = 8


@dataclass(frozen=True)
class LoopOutcome:
    content: str | None
    usage: Usage
    refused_tool_calls: int
    failure: Failure | None = None


def _assistant_message(reply):
    message = {"role": "assistant", "content": reply.content}
    message["tool_calls"] = [
        {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
        for c in reply.tool_calls
    ]
    return message


def _tool_message(call, result):
    return {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)}


class ToolLoop:
    def __init__(self, llm, tool_runner, max_turns=MAX_TURNS):
        self._llm = llm
        self._runner = tool_runner
        self._max_turns = max_turns

    def run(self, model, system_prompt, user_message, tool_definitions):
        """Loop until the model answers without tool calls, or the turn budget runs out."""
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}]
        allowed = {d["function"]["name"] for d in tool_definitions}
        usage, refused = Usage(), 0
        for _ in range(self._max_turns):
            try:
                reply = self._llm.chat(model, list(messages), tool_definitions)
            except LlmError as exc:
                return LoopOutcome(None, usage, refused, Failure(ErrorCode.API_ERROR, str(exc)))
            usage += reply.usage
            if not reply.tool_calls:
                return LoopOutcome(reply.content, usage, refused)
            messages.append(_assistant_message(reply))
            for call in reply.tool_calls:
                if call.name in allowed:
                    result = self._runner.run(call.name, call.arguments)
                else:
                    refused += 1
                    result = {"ok": False, "error_code": str(ErrorCode.EXTRA_TOOL_CALL),
                              "detail": f"tool {call.name!r} is not available to this agent"}
                messages.append(_tool_message(call, result))
        detail = f"no final answer within {self._max_turns} turns"
        return LoopOutcome(None, usage, refused, Failure(ErrorCode.TOOL_BUDGET_EXCEEDED, detail))
