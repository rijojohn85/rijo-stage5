"""Executes one model-requested tool call safely.

Arguments are checked against the tool's declared parameters first. Without that, a model
could pass `root=...` (a Python keyword of the tool functions) and escape the sandbox.
"""
import json

from harness import tools
from harness.tool_schemas import TOOL_SPECS


def _bad_argument(detail):
    return {"ok": False, "error_code": "BAD_ARGUMENT", "detail": detail}


class ToolRunner:
    def __init__(self, root=tools.PROJECT_ROOT, registry=None, specs=None):
        self._root = root
        self._registry = tools.TOOLS if registry is None else registry
        self._specs = TOOL_SPECS if specs is None else specs

    def run(self, name, raw_arguments):
        """Return the tool's result dict. Never raises."""
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as exc:
            return _bad_argument(f"arguments are not valid JSON: {exc}")
        problem = self._argument_problem(name, arguments)
        if problem:
            return _bad_argument(problem)
        return self._registry[name](**arguments, root=self._root)

    def _argument_problem(self, name, arguments):
        if not isinstance(arguments, dict):
            return "arguments must be a JSON object"
        parameters = self._specs[name]["parameters"]
        unknown = set(arguments) - set(parameters["properties"])
        if unknown:
            return f"unknown argument(s): {', '.join(sorted(unknown))}"
        missing = set(parameters["required"]) - set(arguments)
        if missing:
            return f"missing argument(s): {', '.join(sorted(missing))}"
        return None
