from types import SimpleNamespace

from harness import tools
from harness.hashing import sha256_json
from harness.llm import FireworksClient, Usage
from harness.manifest import load_agent_specs
from harness.tool_schemas import TOOL_SPECS, tool_definitions


def test_every_real_tool_has_a_schema_and_no_more():
    assert set(TOOL_SPECS) == set(tools.TOOLS)


def test_tool_definitions_come_from_the_manifest():
    for name, spec in load_agent_specs().items():
        assert [d["function"]["name"] for d in tool_definitions(spec.tools)] == list(spec.tools)


def test_hash_ignores_key_order():
    assert sha256_json({"a": 1, "b": 2}) == sha256_json({"b": 2, "a": 1})


def test_usage_adds():
    assert Usage(1, 2) + Usage(3, 4) == Usage(4, 6)


def test_fireworks_reply_conversion_without_network():
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="read_file", arguments='{"path": "x"}'))
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]))],
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3))
    reply = FireworksClient._to_reply(response)
    assert reply.tool_calls[0].name == "read_file" and reply.usage == Usage(7, 3)
