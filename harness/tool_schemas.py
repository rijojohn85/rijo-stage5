"""OpenAI-format tool definitions for the real tools. Which agent gets which comes from the manifest."""

TOOL_SPECS = {
    "read_file": {
        "description": "Read a UTF-8 text file inside sandbox/. Read-only.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Project-relative path, e.g. sandbox/demo-app/calc.py"}},
            "required": ["path"],
        },
    },
    "edit_file": {
        "description": "Replace exactly one occurrence of old_text with new_text in a file inside sandbox/.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string", "description": "Text to replace; must occur exactly once"},
                "new_text": {"type": "string"},
            },
            "required": ["path", "old_text", "new_text"],
        },
    },
    "run_test": {
        "description": "Run the plan's test command (no shell) and return its exit code and output.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
}


def tool_definitions(tool_names):
    """Definitions for exactly the named tools, so an agent can never be shown an unlisted one."""
    return [
        {"type": "function", "function": {"name": name, **TOOL_SPECS[name]}}
        for name in tool_names
    ]
