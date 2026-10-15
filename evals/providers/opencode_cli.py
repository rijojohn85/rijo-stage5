#!/usr/bin/env python3
"""promptfoo provider: run one agent prompt through the opencode CLI.

The eval must use the same engine as the skills, so this shells out to
`opencode run --format json` and then reads the real token usage and cost for that
session from the local opencode server (`opencode api get /api/session`).

Live logging is deliberate: a hung call must be visibly hung, not silent. Every call
writes JSONL lifecycle records to `evals/call-events.log` and echoes a readable line to
stderr (promptfoo lifts worker stderr into the eval log). Heartbeats show that a slow
call is still doing something.

Output is returned verbatim. It is deliberately NOT cleaned of prose or code fences:
if a prompt lets the model wrap its JSON, the eval should fail, not hide it.
"""

import json
import os
import re
import selectors
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CALL_EVENTS_LOG = REPO / "evals" / "call-events.log"
USAGE_LOG = REPO / "evals" / "usage.log"
DEFAULT_MODEL = "opencode-go/deepseek-v4.1-flash"
HEARTBEAT_S = 15.0
# A model that wraps otherwise-correct JSON in a markdown fence. This is a cosmetic
# wrapper, not a wrong answer: the object inside is complete and valid. Stripping it is
# disclosed, not silent -- every occurrence is counted in call-events.log (`fenced`), so the
# non-compliance rate is visible rather than erased. The reference passing submission
# (/home/rijojohn/Downloads/akash-garg-stage4-submission-final) does the same, calling it a
# defensive unwrap in src/agents.py.
FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_event(phase, **fields):
    """One lifecycle record to the JSONL audit log and to stderr. Never raises."""
    entry = {"ts": _now(), "phase": phase}
    entry.update(fields)
    try:
        CALL_EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(CALL_EVENTS_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:  # noqa: BLE001
        pass
    try:
        detail = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
        print(f"[triage-eval] {entry['ts']} {phase} {detail}".rstrip(), file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001
        pass
    return entry


def get_usage(session_id):
    """(tokens, cost) for a session, or (None, None). Unknown stays None."""
    if not session_id:
        return None, None
    try:
        out = subprocess.run(
            ["opencode", "api", "get", "/api/session"],
            capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
        )
        data = json.loads(out.stdout or "null")
        rows = data.get("data", data) if isinstance(data, dict) else data
        for session in rows or []:
            if session.get("id") == session_id:
                tokens = session.get("tokens")
                return (tokens if isinstance(tokens, dict) else None), session.get("cost")
    except Exception as exc:  # noqa: BLE001 - usage lookup must never break an eval
        log_event("usage_error", session_id=session_id, error=str(exc)[:200])
    return None, None


def _describe(line):
    """(kind, extra) for one NDJSON line, for the live log."""
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None, {}
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return None, {}
    kind = event.get("type")
    if not kind:
        return None, {}
    extra = {}
    part = event.get("part") or {}
    if part.get("type") == "tool":
        extra["tool"] = part.get("tool")
    if kind == "error":
        err = event.get("error") or {}
        extra["error"] = str(err.get("message") or err.get("type"))[:160]
    return kind, extra


def call_opencode(prompt, model, cwd, timeout, label="case"):
    """Run one opencode turn. Returns (output, session_id, elapsed_seconds).

    `--auto` is required: the prompt tells the agent to read the repo, and without
    auto-approval the non-interactive run blocks on the permission prompt forever.
    stdin is /dev/null so the child can never inherit (and wait on) the worker's pipe.
    """
    args = ["opencode", "run", "-m", model, "--auto", "--format", "json", prompt]
    started = time.time()
    deadline = started + timeout
    log_event("call_start", label=label, model=model, prompt_chars=len(prompt),
              timeout_s=int(timeout), cwd=str(cwd))

    # opencode reads the inherited $PWD, NOT the process cwd, to decide which project it is
    # working in. `cwd=` alone is not enough: without this the agent silently edits the
    # parent's directory. Verified 2026-10-15.
    env = {**os.environ, "PWD": str(cwd)}
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, text=True, cwd=str(cwd), env=env,
    )
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    lines, kinds, timed_out = [], {}, False
    last_beat = started
    try:
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                timed_out = True
                proc.kill()
                break
            if proc.poll() is not None:
                tail = proc.stdout.read()
                if tail:
                    lines.extend(tail.splitlines())
                break
            if sel.select(timeout=min(5.0, remaining)):
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.rstrip("\n")
                lines.append(line)
                kind, extra = _describe(line)
                if kind:
                    kinds[kind] = kinds.get(kind, 0) + 1
                    if kind in ("tool_use", "error"):
                        log_event("call_event", label=label, kind=kind, **extra)
            now = time.time()
            if now - last_beat >= HEARTBEAT_S:
                log_event("heartbeat", label=label, elapsed_s=round(now - started, 1),
                          events=len(lines), kinds=",".join(sorted(kinds)) or None,
                          quiet_s=round(now - last_beat, 1))
                last_beat = now
    finally:
        sel.close()
        try:
            proc.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        if proc.poll() is None:
            proc.wait()
    elapsed = round(time.time() - started, 1)

    if timed_out:
        log_event("call_timeout", label=label, elapsed_s=elapsed, limit_s=int(timeout),
                  events=len(lines), kinds=",".join(sorted(kinds)) or None)
        return f"PROVIDER ERROR: opencode run exceeded {int(timeout)}s", None, elapsed

    texts, session_id, errors = [], None, []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if event.get("sessionID"):
            session_id = event["sessionID"]
        if event.get("type") == "error":
            err = event.get("error") or {}
            errors.append(str(err.get("message") or err.get("type") or event))
        part = event.get("part") or {}
        if part.get("type") == "text" and "text" in part:
            texts.append(part["text"])

    # The model's reply is its LAST text block. Earlier text blocks are narration around
    # tool calls ("I'll start by reading the repo files"), and joining them onto the answer
    # makes a perfectly good reply unparseable. Measured 2026-10-15: a chain step emitted
    # exactly that preamble, which looked like a prompt failure until the raw text was read.
    answers = [t for t in texts if t and t.strip()]

    if errors:
        output = f"PROVIDER ERROR: {'; '.join(errors)[:500]}"
    elif answers:
        output = answers[-1]
    elif proc.returncode not in (0, None):
        output = f"PROVIDER ERROR (rc={proc.returncode})"
    else:
        output = ""

    fenced = False
    if output and not errors:
        wrapped = FENCE_RE.match(output)
        if wrapped:
            output = wrapped.group(1).strip()
            fenced = True

    log_event("call_end", label=label, elapsed_s=elapsed, rc=proc.returncode,
              session_id=session_id, output_chars=len(output), text_parts=len(texts),
              fenced=fenced or None,
              kinds=",".join(f"{k}x{v}" for k, v in sorted(kinds.items())) or None,
              error=None if output and not errors else (output[:160] or "empty output"))
    return output, session_id, elapsed


def log_usage(model, session_id, tokens, cost, elapsed, label):
    entry = {
        "ts": _now(),
        "label": label,
        "model": model,
        "session_id": session_id,
        "input_tokens": None if not tokens else tokens.get("input"),
        "output_tokens": None if not tokens else tokens.get("output"),
        "reasoning_tokens": None if not tokens else tokens.get("reasoning"),
        "cost_usd": cost,
        "elapsed_s": elapsed,
    }
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(USAGE_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        log_event("usage", label=label, input_tokens=entry["input_tokens"],
                  output_tokens=entry["output_tokens"], cost_usd=cost, elapsed_s=elapsed,
                  session_id=session_id)
    except Exception:  # noqa: BLE001 - logging must never break an eval
        pass


def _label(context):
    ctx = context or {}
    expect = (ctx.get("vars") or {}).get("expect") or {}
    if expect.get("classification"):
        return (
            f"{expect.get('classification')}/{expect.get('decision')}/"
            f"esc={expect.get('escalation')}"
        )
    description = str((ctx.get("test") or {}).get("description") or "").strip()
    if description:
        return description[:70]
    return str(expect.get("schema") or "case")


def call_api(prompt, options, context):
    cfg = (options or {}).get("config") or {}
    model = cfg.get("model") or DEFAULT_MODEL
    # promptfoo owns `config.timeout` as the Python worker budget in milliseconds.
    # `call_timeout` is our own, shorter subprocess budget, so a hang is reported as a
    # provider error instead of taking the worker down with it.
    timeout_s = (cfg.get("call_timeout") or cfg.get("timeout") or 240_000) / 1000
    # `workspace` is a path relative to the repo. Evals that edit files point at a scratch
    # copy so that the real sandbox is left untouched and a re-run starts from the bug.
    if cfg.get("workspace"):
        cwd = REPO / cfg["workspace"]
    elif cfg.get("cwd"):
        cwd = Path(cfg["cwd"])
    else:
        cwd = REPO
    label = _label(context)

    output, session_id, elapsed = call_opencode(prompt, model, cwd, timeout_s, label=label)
    tokens, cost = get_usage(session_id)
    log_usage(model, session_id, tokens, cost, elapsed, label)

    result = {"output": output, "cost": cost, "sessionId": session_id}
    if tokens:
        cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
        t_in = tokens.get("input")
        t_out = tokens.get("output")
        t_reason = tokens.get("reasoning") or 0
        t_cache = cache.get("read") or 0
        if isinstance(t_in, (int, float)) and isinstance(t_out, (int, float)):
            result["tokenUsage"] = {
                "prompt": int(t_in) + int(t_cache),
                "completion": int(t_out) + int(t_reason),
            }
    return result


if __name__ == "__main__":
    sys.stderr.write("internal provider; call via promptfoo\n")
    sys.exit(2)
