import os
import shutil
import subprocess
import sys

import pytest
from harness import tools


@pytest.fixture
def root(tmp_path):
    (tmp_path / "sandbox" / "app").mkdir(parents=True)
    (tmp_path / "sandbox" / "app" / "a.py").write_text("x = 1\n")
    (tmp_path / "secret.txt").write_text("top secret")
    return tmp_path


def refused(res):
    return res["ok"] is False and res["error_code"] == "OUTSIDE_SANDBOX"


def test_read_inside(root):
    r = tools.read_file("sandbox/app/a.py", root=root)
    assert r == {"ok": True, "path": "sandbox/app/a.py", "content": "x = 1\n"}


def test_read_missing_lists_only_sandbox_files(root):
    (root / "sandbox/app/b.txt").write_text("second")
    r = tools.read_file("sandbox/README.md", root=root)
    assert r == {"ok": False, "error_code": "NOT_FOUND",
                 "detail": "not a file: sandbox/README.md. Files available: sandbox/app/a.py, sandbox/app/b.txt"}
    assert "secret.txt" not in r["detail"]
    assert str(root) not in r["detail"]


def test_read_real_sandbox_missing_lists_existing_files():
    r = tools.read_file("sandbox/nonexistent-file")
    assert r["error_code"] == "NOT_FOUND"
    listing = r["detail"].split("Files available: ")[1].split(", ")
    for path in ("sandbox/demo-app/SPEC.md", "sandbox/demo-app/calc.py", "sandbox/demo-app/storage.py"):
        assert path in listing
    assert listing == sorted(listing)
    assert all(path.startswith("sandbox/") and (tools.PROJECT_ROOT / path).is_file() for path in listing)


def test_read_directory_lists_only_files_under_that_directory(root):
    (root / "sandbox/other.txt").write_text("outside requested directory")
    (root / "sandbox/app/nested").mkdir()
    (root / "sandbox/app/nested/b.txt").write_text("nested")
    assert tools.read_file("sandbox/app", root=root) == {
        "ok": True, "path": "sandbox/app", "listing": ["sandbox/app/a.py", "sandbox/app/nested/b.txt"]}


def test_read_listings_skip_symlinks_caches_and_git_metadata(root):
    os.symlink(root / "secret.txt", root / "sandbox/link.txt")
    os.symlink(root, root / "sandbox/dirlink")
    os.symlink(root / "sandbox", root / "sandbox/loop")
    (root / "sandbox/app/ignored.pyc").write_bytes(b"cache")
    (root / "sandbox/app/.git").write_text("gitdir: private metadata")
    for name in ("__pycache__", ".git"):
        directory = root / "sandbox" / name
        directory.mkdir()
        (directory / "hidden.txt").write_text("hidden")
        assert tools.read_file(f"sandbox/{name}", root=root)["listing"] == []
    assert tools.read_file("sandbox", root=root)["listing"] == ["sandbox/app/a.py"]
    assert tools.read_file("sandbox/missing", root=root)["detail"].endswith("Files available: sandbox/app/a.py")


def test_read_listings_are_capped_and_note_truncation(root):
    for index in range(60):
        (root / "sandbox/app" / f"file-{index:02}.txt").write_text("file")
    r = tools.read_file("sandbox", root=root)
    expected = ["sandbox/app/a.py"] + [f"sandbox/app/file-{index:02}.txt" for index in range(49)]
    assert r["listing"] == expected
    assert r["detail"] == "Listing truncated to 50 files."
    missing = tools.read_file("sandbox/missing", root=root)
    assert missing["error_code"] == "NOT_FOUND"
    assert missing["detail"] == ("not a file: sandbox/missing. Files available: "
                                 + ", ".join(expected) + ". Listing truncated to 50 files.")


def test_read_listing_errors_do_not_raise(root, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("listing failed")
    monkeypatch.setattr(tools.os, "walk", fail)
    assert tools.read_file("sandbox", root=root)["error_code"] == "TOOL_ERROR"
    assert tools.read_file("sandbox/missing", root=root)["error_code"] == "TOOL_ERROR"


def test_read_traversal_refused(root):
    assert refused(tools.read_file("sandbox/../secret.txt", root=root))
    assert refused(tools.read_file("../secret.txt", root=root))


def test_read_absolute_outside_refused(root):
    assert refused(tools.read_file(str(root / "secret.txt"), root=root))
    assert refused(tools.read_file("/etc/passwd", root=root))


def test_read_symlink_escape_refused(root):
    os.symlink(root / "secret.txt", root / "sandbox" / "link.txt")
    assert refused(tools.read_file("sandbox/link.txt", root=root))
    os.symlink(root, root / "sandbox" / "dirlink")
    assert refused(tools.read_file("sandbox/dirlink/secret.txt", root=root))


def test_edit_outside_refused_and_untouched(root):
    assert refused(tools.edit_file("secret.txt", "top", "TOP", root=root))
    assert refused(tools.edit_file("sandbox/../secret.txt", "top", "TOP", root=root))
    os.symlink(root / "secret.txt", root / "sandbox" / "link.txt")
    assert refused(tools.edit_file("sandbox/link.txt", "top", "TOP", root=root))
    assert (root / "secret.txt").read_text() == "top secret"


def test_edit_inside(root):
    r = tools.edit_file("sandbox/app/a.py", "x = 1", "x = 2", root=root)
    assert r["ok"] and (root / "sandbox/app/a.py").read_text() == "x = 2\n"


def test_edit_errors_structured(root):
    assert tools.edit_file("sandbox/app/a.py", "nope", "y", root=root)["error_code"] == "NO_MATCH"
    assert tools.edit_file("sandbox/app/a.py", "", "y", root=root)["error_code"] == "BAD_ARGUMENT"


def test_bad_inputs_do_not_raise(root):
    assert tools.read_file(None, root=root)["ok"] is False
    assert tools.read_file("sandbox/missing.py", root=root)["error_code"] == "NOT_FOUND"
    assert tools.edit_file(None, "a", "b", root=root)["ok"] is False
    assert tools.run_test(None, root=root)["ok"] is False


def add_test(root, name, body, directory="demo"):
    folder = root / "sandbox" / directory
    folder.mkdir(parents=True, exist_ok=True)
    (root / "sandbox" / "pytest.ini").write_text("[pytest]\n")
    (folder / name).write_text(body)
    return f"sandbox/{directory}/{name}"


def failed(res, code):
    return res["ok"] is False and res["error_code"] == code and isinstance(res["detail"], str)


def test_run_test_pass_and_fail(root):
    green = add_test(root, "test_ok.py", "def test_ok():\n    assert 1 + 1 == 2\n")
    red = add_test(root, "test_bad.py", "def test_bad():\n    assert 1 + 1 == 3\n")
    ok = tools.run_test([green], root=root)
    assert ok["ok"] and ok["passed"] is True and ok["exit_code"] == 0 and "1 passed" in ok["stdout"]
    bad = tools.run_test([red], root=root)
    assert bad["ok"] and bad["passed"] is False and bad["exit_code"] == 1
    both = tools.run_test([green, red], root=root)
    assert both["ok"] and both["exit_code"] == 1


def test_run_test_really_runs_pytest_and_leaves_no_cache_behind(root):
    green = add_test(root, "test_ok.py", "def test_ok():\n    assert True\n")
    assert tools.run_test([green], root=root)["passed"] is True
    leftovers = [p.name for p in (root / "sandbox").rglob("*") if p.name in {".pytest_cache", "__pycache__"}]
    assert leftovers == []


def test_run_test_runs_the_interpreter_with_harness_built_arguments(root, monkeypatch):
    green = add_test(root, "test_ok.py", "def test_ok():\n    assert True\n")
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["cwd"] = argv, kwargs["cwd"]
        return subprocess.CompletedProcess(argv, 5, "no tests ran", "")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    result = tools.run_test([green], root=root)
    assert seen["argv"] == [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", green]
    assert seen["cwd"] == str(root)
    assert result["ok"] and result["exit_code"] == 5 and result["passed"] is False


def test_run_test_timeout(root):
    slow = add_test(root, "test_slow.py", "import time\n\n\ndef test_slow():\n    time.sleep(10)\n")
    assert failed(tools.run_test([slow], root=root, timeout=1), "TIMEOUT")


@pytest.mark.parametrize("files", [
    "sandbox/demo/test_ok.py",                  # a string, not a list
    [],
    None,
    [3],
    ["-x"],
    ["sandbox/demo/test_ok.py", "-x"],
    ["sandbox/demo/test_ok.py::test_ok"],        # node ids are not allowed
    ["sandbox/demo/a.txt"],                      # not a .py file
    ["sandbox/demo/helper.py"],                  # not a test_*.py name
    ["sandbox/demo/test_ok.py\n"],
    ["sandbox/demo/test ok.py"],
    ["sandbox/demo/../demo/test_ok.py"],
    ["../test_x.py"],
    ["sandbox/../test_x.py"],
    ["/etc/test_passwd.py"],
    ["demo/test_ok.py"],                         # not under sandbox/
    ["sandbox/demo/test_ok.py"] * 4,             # more than 3
])
def test_run_test_refuses_anything_but_test_files(root, files):
    add_test(root, "test_ok.py", "def test_ok():\n    assert True\n")
    assert failed(tools.run_test(files, root=root), "BAD_ARGUMENT")


def test_run_test_refuses_missing_file_and_symlinks(root, tmp_path_factory):
    assert failed(tools.run_test(["sandbox/demo/test_none.py"], root=root), "NOT_FOUND")
    outside = tmp_path_factory.mktemp("outside")
    (outside / "test_evil.py").write_text("def test_evil():\n    assert True\n")
    (root / "sandbox" / "demo").mkdir()
    os.symlink(outside / "test_evil.py", root / "sandbox" / "demo" / "test_link.py")
    assert refused(tools.run_test(["sandbox/demo/test_link.py"], root=root))
    os.symlink(outside, root / "sandbox" / "elsewhere")
    assert refused(tools.run_test(["sandbox/elsewhere/test_evil.py"], root=root))


def test_run_test_reports_a_missing_pytest_instead_of_a_red_test(root, monkeypatch):
    green = add_test(root, "test_ok.py", "def test_ok():\n    assert True\n")
    monkeypatch.setattr(tools.importlib.util, "find_spec", lambda name: None)
    assert failed(tools.run_test([green], root=root), "PYTEST_MISSING")


def test_run_test_runs_a_real_sandbox_test_file_under_the_pinned_rootdir(tmp_path):
    """The committed sandbox/pytest.ini, copied into a scratch root, is the config pytest picks."""
    scratch = tmp_path / "scratch"
    (scratch / "sandbox" / "demo").mkdir(parents=True)
    shutil.copy(tools.PROJECT_ROOT / "sandbox" / "pytest.ini", scratch / "sandbox" / "pytest.ini")
    (scratch / "pytest.ini").write_text("[pytest]\naddopts = --definitely-not-an-option\n")  # above sandbox/: ignored
    (scratch / "sandbox" / "demo" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    result = tools.run_test(["sandbox/demo/test_ok.py"], root=scratch)
    assert result["ok"] and result["passed"] is True, result
    assert "rootdir: " + str(scratch / "sandbox") in subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "sandbox/demo/test_ok.py"],
        cwd=scratch, capture_output=True, text=True, env={"PYTHONDONTWRITEBYTECODE": "1"}).stdout


def test_real_sandbox_has_the_pinned_pytest_ini():
    assert (tools.PROJECT_ROOT / "sandbox" / "pytest.ini").read_text().startswith("[pytest]")


# --- create_file ------------------------------------------------------------------------------

NEW_TEST = "def test_new():\n    assert True\n"


def test_create_file_creates_a_new_test_file(root):
    (root / "sandbox" / "demo").mkdir()
    result = tools.create_file("sandbox/demo/test_new.py", NEW_TEST, root=root)
    assert result == {"ok": True, "path": "sandbox/demo/test_new.py", "bytes": len(NEW_TEST)}
    assert (root / "sandbox/demo/test_new.py").read_text() == NEW_TEST


@pytest.mark.parametrize("path", [
    "sandbox/demo/x.py",
    "sandbox/demo/test_new.txt",
    "sandbox/demo/new_test.py",
    "sandbox/demo/test_.py",
    "sandbox/demo/test_new.py\n",
    "sandbox/demo/test-new.py",
    "../test_x.py",
    "sandbox/../test_x.py",
    "sandbox/demo/../test_x.py",
    "/tmp/sandbox/test_x.py",
    "test_x.py",
    "secret/test_x.py",
    "",
    None,
    7,
])
def test_create_file_refuses_non_test_paths_and_escapes(root, path):
    (root / "sandbox" / "demo").mkdir()
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))
    result = tools.create_file(path, NEW_TEST, root=root)
    assert result["ok"] is False and result["error_code"] in {"BAD_ARGUMENT", "OUTSIDE_SANDBOX"}
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*")) == before


def test_create_file_never_overwrites_a_file_or_a_symlink(root):
    demo = root / "sandbox" / "demo"
    demo.mkdir()
    (demo / "test_old.py").write_text("original\n")
    assert failed(tools.create_file("sandbox/demo/test_old.py", NEW_TEST, root=root), "FILE_EXISTS")
    assert (demo / "test_old.py").read_text() == "original\n"
    os.symlink(root / "secret.txt", demo / "test_link.py")
    assert failed(tools.create_file("sandbox/demo/test_link.py", NEW_TEST, root=root), "FILE_EXISTS")
    os.symlink(root / "nowhere.txt", demo / "test_dangling.py")
    assert failed(tools.create_file("sandbox/demo/test_dangling.py", NEW_TEST, root=root), "FILE_EXISTS")
    assert (root / "secret.txt").read_text() == "top secret" and not (root / "nowhere.txt").exists()


def test_create_file_refuses_a_missing_parent_and_never_makes_directories(root):
    assert failed(tools.create_file("sandbox/missing/test_new.py", NEW_TEST, root=root), "NOT_FOUND")
    assert not (root / "sandbox" / "missing").exists()


def test_create_file_refuses_a_symlinked_parent(root, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    os.symlink(outside, root / "sandbox" / "elsewhere")
    assert refused(tools.create_file("sandbox/elsewhere/test_new.py", NEW_TEST, root=root))
    assert list(outside.iterdir()) == []
    os.symlink(root / "sandbox" / "app", root / "sandbox" / "alias")  # inside sandbox/, still a symlink
    assert refused(tools.create_file("sandbox/alias/test_new.py", NEW_TEST, root=root))
    assert not (root / "sandbox" / "app" / "test_new.py").exists()


def test_create_file_refuses_oversize_and_non_string_content(root):
    (root / "sandbox" / "demo").mkdir()
    assert failed(tools.create_file("sandbox/demo/test_big.py", "x" * (tools.MAX_CREATE_BYTES + 1), root=root), "TOO_LARGE")
    assert failed(tools.create_file("sandbox/demo/test_none.py", None, root=root), "BAD_ARGUMENT")
    assert not list((root / "sandbox" / "demo").iterdir())
    assert tools.create_file("sandbox/demo/test_max.py", "x" * tools.MAX_CREATE_BYTES, root=root)["ok"]


def test_create_file_never_raises(root, monkeypatch):
    (root / "sandbox" / "demo").mkdir()

    def boom(*args, **kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr("builtins.open", boom)
    result = tools.create_file("sandbox/demo/test_new.py", NEW_TEST, root=root)
    assert failed(result, "TOOL_ERROR")


def test_create_file_cannot_reach_other_agents_files(root):
    """Only a new test_*.py can be created, so it cannot be used to rewrite code or decisions."""
    (root / "sandbox" / "demo").mkdir()
    for path in ("decisions/decisions.jsonl", "sandbox/demo/calc.py", "sandbox/pytest.ini", "sandbox/demo/conftest.py"):
        assert tools.create_file(path, "x", root=root)["ok"] is False


def test_tool_results_have_the_documented_shape(root):
    (root / "sandbox" / "demo").mkdir()
    green = add_test(root, "test_ok.py", "def test_ok():\n    assert True\n")
    results = [tools.create_file("sandbox/demo/test_new.py", NEW_TEST, root=root),
               tools.create_file("sandbox/demo/x.py", NEW_TEST, root=root),
               tools.run_test([green], root=root), tools.run_test(["-x"], root=root)]
    for result in results:
        assert isinstance(result["ok"], bool)
        if not result["ok"]:
            assert set(result) == {"ok", "error_code", "detail"}
