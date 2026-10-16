from pathlib import Path
import yaml
import pytest
from harness import tools

ROOT = Path(__file__).resolve().parent.parent
LAUNCH = ["launch_screen", "launch_triage", "launch_plan", "launch_implement",
          "launch_reviewer", "launch_escalate", "launch_writer"]
# Exact expectation from plan Section 3.
EXPECTED_TOOLS = {
    "screen": [], "triage": ["read_file"], "plan": ["read_file"],
    "implement": ["read_file", "edit_file", "run_test"],
    "reviewer": ["read_file"], "escalate": [], "writer": [],
}
REVIEWED = ["screen", "triage", "plan", "implement", "escalate", "writer"]


@pytest.fixture(scope="module")
def m():
    return yaml.safe_load((ROOT / "manifest.yaml").read_text())


def test_top_level_keys(m):
    assert set(m) == {"coordinator", "agents"}
    assert set(m["agents"]) == set(EXPECTED_TOOLS)


def test_coordinator_only_launch_tools(m):
    t = m["coordinator"]["tools"]
    assert sorted(t) == sorted(LAUNCH)
    assert all(x.startswith("launch_") for x in t)


def test_no_real_tool_on_coordinator(m):
    assert not set(m["coordinator"]["tools"]) & set(tools.TOOLS)


def test_reviewer_only_read_file(m):
    assert m["agents"]["reviewer"]["tools"] == ["read_file"]


def test_screen_escalate_and_writer_have_no_tools(m):
    assert m["agents"]["screen"]["tools"] == []
    assert m["agents"]["escalate"]["tools"] == []
    assert m["agents"]["writer"]["tools"] == []


def test_every_agent_tool_is_known_real_tool(m):
    for name, a in m["agents"].items():
        assert set(a["tools"]) <= set(tools.TOOLS), name


def test_no_extras_vs_plan(m):
    for name, a in m["agents"].items():
        assert sorted(a["tools"]) == sorted(EXPECTED_TOOLS[name]), name


def test_only_implementer_has_mutation_tools(m):
    for name, a in m["agents"].items():
        has = {"edit_file", "run_test"} & set(a["tools"])
        assert bool(has) == (name == "implement"), name


def test_all_isolated_context(m):
    for name, a in m["agents"].items():
        assert a["isolated_context"] is True, name


def test_reviewer_reviews_exactly_six(m):
    assert sorted(m["agents"]["reviewer"]["reviews"]) == sorted(REVIEWED)
    for name, a in m["agents"].items():
        assert ("reviews" in a) == (name == "reviewer")


def test_models_concrete_full_ids(m):
    models = [m["coordinator"]["model"]] + [a["model"] for a in m["agents"].values()]
    for x in models:
        assert x.startswith("accounts/fireworks/models/") and x != "auto"
    assert m["coordinator"]["model"].endswith("glm-5p3")
    assert all(a["model"].endswith("deepseek-v4p1-flash") for a in m["agents"].values())


def test_workflow_eval_results_fields(m):
    wf = {"screen": "screen", "triage": "triage", "plan": "plan", "implement": "implement",
          "reviewer": "review", "escalate": "escalate", "writer": "writer"}
    for name, a in m["agents"].items():
        assert a["workflow"] == wf[name]
        assert a["eval"] == f"evals/promptfoo.{a['workflow']}.yaml"
        assert a["results"] == f"results/{a['workflow']}.json"
