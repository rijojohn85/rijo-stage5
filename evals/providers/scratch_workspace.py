"""A throwaway copy of sandbox/ under its own git repository, so an eval can edit freely.

The real sandbox keeps its bugs and every case starts from the same baseline commit. The
copy has the same layout as the project root, so plan paths such as
`sandbox/demo-app/calc.py` and harness.sandbox_git.GitSandbox work unchanged.
"""
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from harness.tools import PROJECT_ROOT, SANDBOX_DIRNAME

GIT_IDENTITY = ("-c", "user.name=eval", "-c", "user.email=eval@example.invalid")


def _git(root, *args):
    subprocess.run(["git", *GIT_IDENTITY, "-C", str(root), *args], check=True, capture_output=True, stdin=subprocess.DEVNULL)


@contextmanager
def scratch_workspace(source_root=PROJECT_ROOT):
    """Yield a temp project root holding a committed copy of <source_root>/sandbox."""
    with tempfile.TemporaryDirectory(prefix="eval-workspace-") as directory:
        root = Path(directory)
        shutil.copytree(Path(source_root) / SANDBOX_DIRNAME, root / SANDBOX_DIRNAME,
                        ignore=shutil.ignore_patterns("__pycache__"))
        _git(root, "init", "-q")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "baseline")
        yield root
