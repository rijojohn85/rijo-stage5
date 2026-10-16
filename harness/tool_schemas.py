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
    "create_file": {
        "description": ("Create one new pytest file listed in the plan's test_files. Refuses existing files "
                        "and any path that is not sandbox/.../test_*.py."),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string",
                         "description": "a new test file from the plan's test_files, e.g. sandbox/demo-app/test_calc_empty.py"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    "run_test": {
        "description": "Run the plan's test files with pytest; the harness builds the command.",
        "parameters": {
            "type": "object",
            "properties": {
                "files": {"type": "array", "items": {"type": "string"},
                          "description": "the plan's test_files, e.g. sandbox/demo-app/test_calc_empty.py"},
            },
            "required": ["files"],
        },
    },
}


def tool_definitions(tool_names):
    """Definitions for exactly the named tools, so an agent can never be shown an unlisted one."""
    return [
        {"type": "function", "function": {"name": name, **TOOL_SPECS[name]}}
        for name in tool_names
    ]
