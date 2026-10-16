"""Sandboxed tools for sub-agents: read_file, edit_file, create_file, run_test.

Paths are project-root-relative (e.g. "sandbox/demo-app/calc.py", matching the plan's
file list). Every path is resolved to its real location (symlinks followed) and must lie
inside <project root>/sandbox. Every tool returns a dict and never raises:
    {"ok": True, ...}  or  {"ok": False, "error_code": "...", "detail": "..."}
"""
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SANDBOX_DIRNAME = "sandbox"
# The only paths run_test runs and create_file creates. No "..", flags, spaces or node ids.
TEST_FILE_PATTERN = re.compile(r"^sandbox/(?:[A-Za-z0-9_-]+/)*test_[A-Za-z0-9_]+\.py$")
MAX_TEST_FILES = 3
MAX_CREATE_BYTES = 20_000
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


def create_file(path, content, root=PROJECT_ROOT):
    """Create one NEW pytest file (sandbox/.../test_*.py). Never overwrites, never creates directories."""
    try:
        if not isinstance(path, str) or not TEST_FILE_PATTERN.fullmatch(path):
            return _err("BAD_ARGUMENT", "create_file only creates test_*.py files under sandbox/")
        if not isinstance(content, str):
            return _err("BAD_ARGUMENT", "content must be a string")
        data = content.encode("utf-8")
        if len(data) > MAX_CREATE_BYTES:
            return _err("TOO_LARGE", f"content exceeds {MAX_CREATE_BYTES} bytes")
        parent = Path(root) / Path(path).parent
        real_parent, err = _resolve(str(parent), root)
        if err:
            return err
        if parent.is_symlink():
            return _err("OUTSIDE_SANDBOX", f"parent directory is a symlink: {parent.relative_to(root)}")
        if not real_parent.is_dir():
            return _err("NOT_FOUND", f"directory does not exist: {Path(path).parent}")
        target = real_parent / Path(path).name
        if os.path.lexists(target):
            return _err("FILE_EXISTS", f"already exists, create_file never overwrites: {path}")
        try:
            with open(target, "x", encoding="utf-8") as handle:  # exclusive create: a race cannot overwrite
                handle.write(content)
        except FileExistsError:
            return _err("FILE_EXISTS", f"already exists, create_file never overwrites: {path}")
        return {"ok": True, "path": path, "bytes": len(data)}
    except Exception as exc:
        return _err("TOOL_ERROR", f"{type(exc).__name__}: {exc}")


def _test_files_problem(files, root):
    """An error dict when `files` is not 1 to MAX_TEST_FILES existing test files inside sandbox/, else None."""
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_TEST_FILES:
        return _err("BAD_ARGUMENT", f"files must be a list of 1 to {MAX_TEST_FILES} test file paths")
    for name in files:
        if not isinstance(name, str) or not TEST_FILE_PATTERN.fullmatch(name):
            return _err("BAD_ARGUMENT", f"not a sandbox/.../test_*.py path: {name!r}")
        if (Path(root) / name).is_symlink():
            return _err("OUTSIDE_SANDBOX", f"test file is a symlink: {name}")
        real, err = _resolve(name, root)
        if err:
            return err
        if not real.is_file():
            return _err("NOT_FOUND", f"not a file: {name}")
    return None


def run_test(files, root=PROJECT_ROOT, timeout=TEST_TIMEOUT_SECONDS):
    """Run the plan's pytest files. The harness builds the command; the model only names files.

    `files` holds 1 to 3 existing sandbox/.../test_*.py paths. No flags, node ids or other
    arguments get through. Honest limit: this narrows the command line only. pytest still
    executes whatever Python the test file contains, so it is not a security boundary.
    The backstops stay the plan-scope check and `git status`.
    """
    try:
        problem = _test_files_problem(files, root)
        if problem:
            return problem
        if importlib.util.find_spec("pytest") is None:
            return _err("PYTEST_MISSING", "pytest is not installed for this interpreter")
        argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files]
        try:
            proc = subprocess.run(
                argv, cwd=str(root), capture_output=True, text=True,
                timeout=timeout, stdin=subprocess.DEVNULL,
                env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"},
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


TOOLS = {"read_file": read_file, "edit_file": edit_file, "create_file": create_file, "run_test": run_test}
