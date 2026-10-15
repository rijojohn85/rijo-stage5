"""Step 0 smoke test: each model must return a reply and a tool call. Never prints the key."""
import json, os, sys
from openai import OpenAI

MODELS = ["accounts/fireworks/models/deepseek-v4p1-flash", "accounts/fireworks/models/glm-5p3"]
TOOLS = [{"type": "function", "function": {
    "name": "read_file", "description": "Read a file inside the sandbox.",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]

client = OpenAI(api_key=os.environ["FIREWORKS_API_KEY"], base_url=os.environ["FIREWORKS_BASE_URL"])
ok = True
for m in MODELS:
    try:
        r = client.chat.completions.create(
            model=m, tools=TOOLS, max_tokens=512,
            messages=[{"role": "user", "content": "Call read_file for sandbox/demo-app/calc.py."}])
        msg = r.choices[0].message
        calls = msg.tool_calls or []
        good = bool(calls) and calls[0].function.name == "read_file"
        print(m, "PASS" if good else "FAIL",
              "tool_call=" + (f"{calls[0].function.name}({calls[0].function.arguments})" if calls else "none"),
              f"tokens in/out={r.usage.prompt_tokens}/{r.usage.completion_tokens}")
        ok &= good
    except Exception as e:
        print(m, "FAIL", type(e).__name__, str(e)[:200].replace(os.environ["FIREWORKS_API_KEY"], "***"))
        ok = False
sys.exit(0 if ok else 1)
