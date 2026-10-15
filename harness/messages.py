"""OpenAI-format chat messages shared by the helper tool loop and the coordinator loop."""
import json


def assistant_message(reply):
    message = {"role": "assistant", "content": reply.content}
    message["tool_calls"] = [
        {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
        for c in reply.tool_calls
    ]
    return message


def tool_message(call, result):
    return {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)}
