"""GitSandbox in a throwaway repository: the diff must include untracked files and leave the index alone."""
import subprocess

import pytest

from harness.hashing import sha256_text
from harness.sandbox_git import GitSandbox


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "sandbox").mkdir()
    (tmp_path / "sandbox" / "a.py").write_text("x = 1\n", encoding="utf-8")
    git(tmp_path, "init", "-q")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@example.com", "add", ".")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "base")
    return tmp_path


def test_untracked_file_content_appears_in_the_diff(repo):
    (repo / "sandbox" / "new_test.py").write_text("def test_it():\n    assert True\n", encoding="utf-8")
    diff = GitSandbox(repo).diff()
    assert "sandbox/new_test.py" in diff and "+def test_it():" in diff


def test_diff_hash_changes_when_an_untracked_file_changes(repo):
    new = repo / "sandbox" / "new_test.py"
    new.write_text("one\n", encoding="utf-8")
    before = sha256_text(GitSandbox(repo).diff())
    new.write_text("two\n", encoding="utf-8")
    assert sha256_text(GitSandbox(repo).diff()) != before


def test_tracked_only_change_matches_plain_git_diff(repo):
    (repo / "sandbox" / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert GitSandbox(repo).diff() == git(repo, "diff", "HEAD", "--", "sandbox")


def test_diff_leaves_the_index_untouched(repo):
    (repo / "sandbox" / "new_test.py").write_text("one\n", encoding="utf-8")
    before = git(repo, "status", "--porcelain")
    GitSandbox(repo).diff()
    assert git(repo, "status", "--porcelain") == before
