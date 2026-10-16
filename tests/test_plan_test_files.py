"""Step C2: the plan names `test_files` (new pytest files); the harness, not the model, builds the test command."""
import json

import pytest

from conftest import IMPLEMENT, PLAN, PLAN_FILE, PLAN_TEST_FILE, TRIAGE, json_reply
from harness.agents import PROFILES
from harness.results import ErrorCode
from harness.tools import PROJECT_ROOT, TEST_FILE_PATTERN

SCHEMA = json.loads((PROJECT_ROOT / "schemas" / "plan.json").read_text(encoding="utf-8"))
SCHEMA_FIELD = SCHEMA["properties"]["test_files"]


def plan_run(env, plan):
    env.reviewed("triage", TRIAGE)
    return env.run("plan", json_reply(plan))


def test_schema_requires_test_files_and_drops_the_free_form_test():
    assert "test_files" in SCHEMA["required"] and "test" not in SCHEMA["required"]
    assert "test" not in SCHEMA["properties"]


def test_schema_pattern_equals_the_run_test_pattern():
    assert SCHEMA_FIELD["items"]["pattern"] == TEST_FILE_PATTERN.pattern


def test_plan_with_test_files_is_accepted_and_summarised(env):
    result = plan_run(env, PLAN)
    assert result["ok"]
    assert result["summary"] == f"2 file(s); tests: {PLAN_TEST_FILE}"


def test_old_free_form_test_field_is_refused(env):
    old = {"files": PLAN["files"], "steps": PLAN["steps"], "test": "python3 -m pytest sandbox/demo-app"}
    assert plan_run(env, old)["error_code"] == ErrorCode.SCHEMA_INVALID


@pytest.mark.parametrize("bad", [
    [], ["-x"], ["../test_x.py"], ["sandbox/demo-app/calc.py"], ["sandbox/demo-app/test_x.py::t"],
    ["sandbox/demo-app/test_x.txt"], ["sandbox/demo-app/test_x.py", "sandbox/demo-app/test_x.py"],
    [f"sandbox/demo-app/test_{n}.py" for n in "abcd"], "sandbox/demo-app/test_x.py",
])
def test_schema_refuses_bad_test_files(env, bad):
    files = [PLAN["files"][0], *(bad if isinstance(bad, list) else [])]
    result = plan_run(env, {**PLAN, "files": files, "test_files": bad})
    assert result["error_code"] == ErrorCode.SCHEMA_INVALID


def test_test_file_missing_from_files_is_refused(env):
    result = plan_run(env, {**PLAN, "files": [PLAN["files"][0]]})
    assert result["error_code"] == ErrorCode.SCHEMA_INVALID
    assert PLAN_TEST_FILE in result["detail"] and "files" in result["detail"]


def implement_after_plan(env, changed):
    env.reviewed("triage", TRIAGE)
    env.reviewed("plan", PLAN)
    env.sandbox.changed = changed
    return env.run("implement", json_reply({**IMPLEMENT, "changed": changed}))


def test_plan_scope_covers_the_new_test_file(env):
    assert implement_after_plan(env, [PLAN_FILE, PLAN_TEST_FILE])["ok"]


def test_a_test_file_the_plan_does_not_name_is_out_of_scope(env):
    result = implement_after_plan(env, [PLAN_FILE, "sandbox/demo-app/test_other.py"])
    assert result["error_code"] == ErrorCode.PLAN_SCOPE_VIOLATION and "test_other.py" in result["detail"]


def test_plan_profile_runs_the_test_files_check():
    assert [type(check).__name__ for check in PROFILES["plan"].checks] == ["SchemaCheck", "TestFilesInPlanCheck"]


@pytest.mark.parametrize("name", ["plan", "implement", "review"])
def test_prompts_use_test_files_not_a_test_command(name):
    text = (PROJECT_ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    assert "test_files" in text
    assert "no new files, and" not in text and "plan's `test`" not in text
