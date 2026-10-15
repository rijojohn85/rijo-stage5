"""The coordinator's launch_* tool definitions and argument parsing. It has no other tools.

Which launch tools exist comes from the manifest; this module only describes their arguments.
"""
import json
from dataclasses import dataclass

from harness.results import ErrorCode

BAD_ARGUMENT = "BAD_ARGUMENT"
REVIEW_TARGETS = ("triage", "plan", "implement", "escalate", "writer")
TERMINAL_STATES = ("VERIFIED", "DEFERRED", "HUMAN_PUNCH_OUT")
LAUNCH_PREFIX = "launch_"

_FEEDBACK = {"type": "string", "description": "Optional reviewer findings or stop reason for this agent."}


def _parameters(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required)}


LAUNCH_SPECS = {
    "launch_triage": ("Run the triage agent on the bug report.", _parameters({"feedback": _FEEDBACK})),
    "launch_plan": ("Run the plan agent (needs a fresh, reviewed fix-now triage).",
                    _parameters({"feedback": _FEEDBACK})),
    "launch_implement": ("Run the implement agent (needs a fresh, reviewed plan).",
                         _parameters({"feedback": _FEEDBACK})),
    "launch_escalate": ("Run the escalation brief agent. Put the stop reason in feedback.",
                        _parameters({"feedback": _FEEDBACK})),
    "launch_reviewer": ("Adversarially review the latest artifact of one target.",
                        _parameters({"target": {"enum": list(REVIEW_TARGETS)}}, ["target"])),
    "launch_writer": ("Draft the final report for a proposed terminal state; the harness validates the state.",
                      _parameters({"terminal_state": {"enum": list(TERMINAL_STATES)}, "feedback": _FEEDBACK},
                                  ["terminal_state"])),
}


def launch_definitions(tool_names):
    """Definitions for exactly the named tools (the manifest's coordinator list)."""
    return [{"type": "function", "function": {"name": n, "description": LAUNCH_SPECS[n][0],
                                              "parameters": LAUNCH_SPECS[n][1]}} for n in tool_names]


@dataclass(frozen=True)
class Launch:
    agent: str
    target: str | None = None
    feedback: str | None = None
    terminal_state: str | None = None


def _bad(detail):
    return {"ok": False, "error_code": BAD_ARGUMENT, "detail": detail}


def _argument_problem(name, arguments):
    parameters = LAUNCH_SPECS[name][1]
    if not isinstance(arguments, dict):
        return "arguments must be a JSON object"
    unknown = set(arguments) - set(parameters["properties"])
    if unknown:
        return f"unknown argument(s): {', '.join(sorted(unknown))}"
    missing = set(parameters["required"]) - set(arguments)
    if missing:
        return f"missing argument(s): {', '.join(sorted(missing))}"
    for key, value in arguments.items():
        allowed = parameters["properties"][key].get("enum")
        if allowed and value not in allowed:
            return f"{key} must be one of {', '.join(allowed)}"
        if not allowed and not isinstance(value, str):
            return f"{key} must be a string"
    return None


def parse_launch(name, raw_arguments, allowed_tools):
    """(Launch, None) or (None, error result). An unlisted tool is EXTRA_TOOL_CALL, as in the helper loop."""
    if name not in allowed_tools:
        return None, {"ok": False, "error_code": str(ErrorCode.EXTRA_TOOL_CALL),
                      "detail": f"tool {name!r} is not available to the coordinator"}
    try:
        arguments = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as exc:
        return None, _bad(f"arguments are not valid JSON: {exc}")
    problem = _argument_problem(name, arguments)
    if problem:
        return None, _bad(problem)
    agent = name.removeprefix(LAUNCH_PREFIX)
    return Launch(agent, arguments.get("target"), arguments.get("feedback"), arguments.get("terminal_state")), None
