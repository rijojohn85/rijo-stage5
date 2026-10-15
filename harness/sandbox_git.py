"""Read-only view of what changed under sandbox/, from the project-root git repository."""
import subprocess

from harness.tools import PROJECT_ROOT, SANDBOX_DIRNAME


class SandboxGitError(Exception):
    """git could not report the sandbox state."""


class GitSandbox:
    def __init__(self, root=PROJECT_ROOT):
        self._root = root

    def changed_files(self):
        """Modified (vs HEAD) and untracked files under sandbox/, project-relative."""
        tracked = self._git("diff", "--name-only", "-z", "HEAD", "--", SANDBOX_DIRNAME)
        untracked = self._git("ls-files", "--others", "--exclude-standard", "-z", "--", SANDBOX_DIRNAME)
        return sorted({p for p in (tracked + untracked).split("\0") if p})

    def diff(self):
        """Unified diff of tracked files vs HEAD (untracked files appear in changed_files only)."""
        return self._git("diff", "HEAD", "--", SANDBOX_DIRNAME)

    def _git(self, *args):
        try:
            done = subprocess.run(["git", "-C", str(self._root), *args], capture_output=True, text=True, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SandboxGitError(str(exc)) from exc
        return done.stdout
