"""Sandboxed tools for sub-agents: read_file, edit_file, run_test.

Paths are project-root-relative (e.g. "sandbox/demo-app/calc.py", matching the plan's
file list). Every path is resolved to its real location (symlinks followed) and must lie
inside <project root>/sandbox. Every tool returns a dict and never raises:
    {"ok": True, ...}  or  {"ok": False, "error_code": "...", "detail": "..."}
"""
import os
import shlex
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SANDBOX_DIRNAME = "sandbox"
MAX_READ_BYTES = 200_000
MAX_LISTING_ENTRIES = 50
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


def _list_files(directory, root):
    """Return sorted sandbox-relative files and a truncation flag; never walk symlinks."""
    files = []
    for current, directories, names in os.walk(directory, followlinks=False):
        directories[:] = [name for name in directories
                          if name not in {"__pycache__", ".git"}
                          and not (Path(current) / name).is_symlink()]
        for name in names:
            candidate = Path(current) / name
            if name.endswith(".pyc") or candidate.is_symlink():
                continue
            real, err = _resolve(str(candidate), root)
            if not err and real.is_file():
                relative = real.relative_to(Path(root).resolve())
                if not {"__pycache__", ".git"}.intersection(relative.parts):
                    files.append(relative.as_posix())
    files.sort()
    return files[:MAX_LISTING_ENTRIES], len(files) > MAX_LISTING_ENTRIES


def read_file(path, root=PROJECT_ROOT):
    """Return file text or a successful directory listing; misses list sandbox files."""
    try:
        real, err = _resolve(path, root)
        if err:
            return err
        if real.is_dir():
            listing, truncated = _list_files(real, root)
            result = {"ok": True, "path": path, "listing": listing}
            if truncated:
                result["detail"] = f"Listing truncated to {MAX_LISTING_ENTRIES} files."
            return result
        if not real.is_file():
            listing, truncated = _list_files(Path(root) / SANDBOX_DIRNAME, root)
            detail = f"not a file: {path}. Files available: {', '.join(listing) or '(none)'}"
            if truncated:
                detail += f". Listing truncated to {MAX_LISTING_ENTRIES} files."
            return _err("NOT_FOUND", detail)
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


def run_test(command, root=PROJECT_ROOT, timeout=TEST_TIMEOUT_SECONDS):
    """Run the plan's test command (no shell) with a timeout.

    The working directory is the project root so plan commands that name
    "sandbox/demo-app/..." work verbatim; any path-like argument that resolves outside
    sandbox/ is refused before running.
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
        # Refuse obvious path arguments that point outside the sandbox.
        for arg in argv[1:]:
            if arg.startswith("-") or "\n" in arg or " " in arg or arg.startswith("import"):
                continue
            if arg.startswith("/") or ".." in Path(arg).parts:
                _, err = _resolve(arg, root)
                if err:
                    return err
        try:
            proc = subprocess.run(
                argv, cwd=str(root), capture_output=True, text=True,
                timeout=timeout, stdin=subprocess.DEVNULL,
                env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except subprocess.TimeoutExpired:
            return _err("TIMEOUT", f"test exceeded {timeout}s")
        except FileNotFoundError:
            return _err("COMMAND_NOT_FOUND", f"not found: {argv[0]}")
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
