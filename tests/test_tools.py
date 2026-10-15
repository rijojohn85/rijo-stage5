import os
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
    assert tools.read_file("sandbox/app", root=root)["ok"] is False
    assert tools.edit_file(None, "a", "b", root=root)["ok"] is False
    assert tools.run_test(None, root=root)["ok"] is False


def test_run_test_pass_and_fail(root):
    ok = tools.run_test("python3 -c 'import sys; sys.exit(0)'", root=root)
    assert ok["ok"] and ok["passed"] is True
    bad = tools.run_test("python3 -c 'import sys; sys.exit(3)'", root=root)
    assert bad["ok"] and bad["passed"] is False and bad["exit_code"] == 3


def test_run_test_timeout(root):
    r = tools.run_test("python3 -c 'import time; time.sleep(5)'", root=root, timeout=1)
    assert r["ok"] is False and r["error_code"] == "TIMEOUT"


def test_run_test_outside_path_refused(root):
    assert refused(tools.run_test("python3 /etc/passwd", root=root))
    assert refused(tools.run_test("python3 ../secret.txt", root=root))


def test_run_test_unknown_command(root):
    assert tools.run_test("no-such-cmd-xyz", root=root)["error_code"] == "COMMAND_NOT_FOUND"


def test_real_sandbox_plan_command_runs():
    cmd = ("python3 -c \"import sys; sys.path.insert(0, 'sandbox/demo-app'); "
           "import calc; calc.average\"")
    r = tools.run_test(cmd)
    assert r["ok"] and r["passed"], r
