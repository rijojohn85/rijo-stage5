"""Read-only view of what changed under sandbox/, from the project-root git repository."""
import os
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
        return sorted({p for p in tracked.split("\0") + self._untracked() if p})

    def diff(self):
        """Unified diff vs HEAD of tracked files, then of each untracked file (as a new file).

        The index is never touched: untracked files are diffed with `--no-index` against /dev/null,
        which exits 1 when the files differ.
        """
        tracked = self._git("diff", "HEAD", "--", SANDBOX_DIRNAME)
        new_files = [self._git("diff", "--no-index", "--", os.devnull, path, ok_codes=(0, 1))
                     for path in self._untracked()]
        return tracked + "".join(new_files)

    def _untracked(self):
        listing = self._git("ls-files", "--others", "--exclude-standard", "-z", "--", SANDBOX_DIRNAME)
        return sorted(p for p in listing.split("\0") if p)

    def _git(self, *args, ok_codes=(0,)):
        try:
            done = subprocess.run(["git", "-C", str(self._root), *args], capture_output=True, text=True)
        except OSError as exc:
            raise SandboxGitError(str(exc)) from exc
        if done.returncode not in ok_codes:
            raise SandboxGitError(f"git {' '.join(args)} exited {done.returncode}: {done.stderr.strip()}")
        return done.stdout
