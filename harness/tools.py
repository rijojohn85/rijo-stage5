"""Sandboxed tools for sub-agents: read_file, edit_file, run_test.

Paths are project-root-relative (e.g. "sandbox/demo-app/calc.py", matching the plan's
file list). Every path is resolved to its real location (symlinks followed) and must lie
inside <project root>/sandbox. Every tool returns a dict and never raises:
    {"ok": True, ...}  or  {"ok": False, "error_code": "...", "detail": "..."}

run_test only accepts `python3 -c <snippet>` or `python3 -m pytest <sandbox paths>` and runs
it inside bubblewrap (sandbox/ read-only, tmpfs /tmp, no network, no home, no .env, cleared
env); it refuses to run at all if bwrap is missing. Remaining limit: kernel-level isolation
covers the filesystem and network only. Edits made through edit_file are still bounded just
by the path resolver, so the plan-scope check and git status remain the backstop for them.
"""
import os
import shlex
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SANDBOX_DIRNAME = "sandbox"
MAX_READ_BYTES = 200_000
TEST_TIMEOUT_SECONDS = 60
MAX_OUTPUT_CHARS = 8_000


def _err(code, detail):
    return {"ok": False, "error_code": code, "detail": detail}


def _resolve(path, root):
    """Return (resolved Path, None) or (None, error dict). Refuses anything outside sandbox/."""
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        return None, _err("BAD_PATH", "path must be a non-empty string")
    sandbox = (Path(root) / SANDBOX_DIRNAME).resolve()
    try:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = Path(root) / candidate
        real = candidate.resolve()  # follows symlinks and collapses ..
    except (OSError, RuntimeError, ValueError) as exc:
        return None, _err("BAD_PATH", str(exc))
    if real != sandbox and sandbox not in real.parents:
        return None, _err("OUTSIDE_SANDBOX", f"path is outside {SANDBOX_DIRNAME}/: {path}")
    return real, None


def read_file(path, root=PROJECT_ROOT):
    """Return a file's text. Read-only, sandbox/ only."""
    try:
        real, err = _resolve(path, root)
        if err:
            return err
        if not real.is_file():
            return _err("NOT_FOUND", f"not a file: {path}")
        data = real.read_bytes()
        if len(data) > MAX_READ_BYTES:
            return _err("TOO_LARGE", f"file exceeds {MAX_READ_BYTES} bytes")
        return {"ok": True, "path": path, "content": data.decode("utf-8")}
    except UnicodeDecodeError:
        return _err("NOT_TEXT", f"file is not UTF-8 text: {path}")
    except Exception as exc:  # no exceptions leak
        return _err("TOOL_ERROR", f"{type(exc).__name__}: {exc}")


def edit_file(path, old_text, new_text, root=PROJECT_ROOT):
    """Replace exactly one occurrence of old_text with new_text. sandbox/ only."""
    try:
        real, err = _resolve(path, root)
        if err:
            return err
        if not isinstance(old_text, str) or not isinstance(new_text, str) or not old_text:
            return _err("BAD_ARGUMENT", "old_text must be non-empty; old_text and new_text must be strings")
        if not real.is_file():
            return _err("NOT_FOUND", f"not a file: {path}")
        text = real.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count == 0:
            return _err("NO_MATCH", "old_text not found")
        if count > 1:
            return _err("AMBIGUOUS_MATCH", f"old_text occurs {count} times; make it unique")
        real.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return {"ok": True, "path": path, "replaced": 1}
    except UnicodeDecodeError:
        return _err("NOT_TEXT", f"file is not UTF-8 text: {path}")
    except Exception as exc:
        return _err("TOOL_ERROR", f"{type(exc).__name__}: {exc}")


def _build_inner_argv(argv, root):
    """Validate argv against the allowlist. Returns (inner argv for the jail, None) or (None, err)."""
    if argv[:2] == ["python3", "-c"]:
        if len(argv) != 3:
            return None, _err("COMMAND_NOT_ALLOWED", "python3 -c takes exactly one snippet and no other arguments")
        return list(argv), None
    if argv[:3] == ["python3", "-m", "pytest"]:
        paths = argv[3:]
        if not paths:
            return None, _err("COMMAND_NOT_ALLOWED", "python3 -m pytest requires at least one path")
        inner = ["python3", "-m", "pytest", "-p", "no:cacheprovider"]
        sandbox = (Path(root) / SANDBOX_DIRNAME).resolve()
        for arg in paths:
            if arg.startswith("-"):
                return None, _err("COMMAND_NOT_ALLOWED", "pytest flags are not allowed")
            real, err = _resolve(arg, root)
            if err:
                return None, err
            inner.append(f"{SANDBOX_DIRNAME}/" + str(real.relative_to(sandbox)) if real != sandbox else SANDBOX_DIRNAME)
        return inner, None
    return None, _err("COMMAND_NOT_ALLOWED",
                      "only `python3 -c <snippet>` and `python3 -m pytest <paths>` are allowed")


def run_test(command, root=PROJECT_ROOT, timeout=TEST_TIMEOUT_SECONDS):
    """Run an allowlisted test command (no shell) inside a bubblewrap jail with a timeout.

    Allowed shapes: `python3 -c <snippet>` and `python3 -m pytest <sandbox paths>`.
    Fails closed with BWRAP_UNAVAILABLE if bwrap is missing.
    """
    try:
        if not isinstance(command, str) or not command.strip():
            return _err("BAD_ARGUMENT", "command must be a non-empty string")
        try:
            argv = shlex.split(command)
        except ValueError as exc:
            return _err("BAD_ARGUMENT", f"cannot parse command: {exc}")
        if not argv:
            return _err("BAD_ARGUMENT", "empty command")
        inner, err = _build_inner_argv(argv, root)
        if err:
            return err
        bwrap = shutil.which("bwrap")
        if not bwrap:
            return _err("BWRAP_UNAVAILABLE", "bubblewrap (bwrap) not found; refusing to run tests unsandboxed")
        sandbox = (Path(root) / SANDBOX_DIRNAME).resolve()
        jail = [
            bwrap, "--die-with-parent", "--clearenv",
            "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "HOME", "/tmp",
            "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
            "--ro-bind", "/usr", "/usr", "--symlink", "usr/bin", "/bin",
            "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib64", "/lib64",
            "--ro-bind", str(sandbox), "/work/sandbox",
            "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev",
            "--unshare-all", "--chdir", "/work",
        ] + inner
        try:
            proc = subprocess.run(
                jail, capture_output=True, text=True,
                timeout=timeout, stdin=subprocess.DEVNULL,
                env={"PATH": os.environ.get("PATH", "")},
            )
        except subprocess.TimeoutExpired:
            return _err("TIMEOUT", f"test exceeded {timeout}s")
        except OSError as exc:
            return _err("TOOL_ERROR", str(exc))
        return {
            "ok": True,
            "passed": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": proc.stdout[-MAX_OUTPUT_CHARS:],
            "stderr": proc.stderr[-MAX_OUTPUT_CHARS:],
        }
    except Exception as exc:
        return _err("TOOL_ERROR", f"{type(exc).__name__}: {exc}")


TOOLS = {"read_file": read_file, "edit_file": edit_file, "run_test": run_test}
