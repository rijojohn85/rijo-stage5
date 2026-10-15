"""A sandbox view with a recorded diff, for reviewing a change that was made elsewhere."""


class FixedDiffSandbox:
    """Same interface as harness.sandbox_git.GitSandbox, but answers from fixture data."""

    def __init__(self, changed=(), diff=""):
        self._changed, self._diff = sorted(changed), diff

    def changed_files(self):
        return list(self._changed)

    def diff(self):
        return self._diff
