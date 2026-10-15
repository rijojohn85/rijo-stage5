import json
import os
import shlex
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


def not_allowed(res):
    return res["ok"] is False and res["error_code"] == "COMMAND_NOT_ALLOWED"


@pytest.mark.parametrize("cmd", [
    "no-such-cmd-xyz",
    "ls sandbox",
    "cat /etc/passwd",
    "python3 /etc/passwd",
    "python3 -m http.server",
    "python3 -m pip list",
    "python3 -c 'print(1)' extra",
    "python3 -c 'print(1)' -c 'print(2)'",
    "python3 -I -c 'print(1)'",
    "python3 -m pytest",
])
def test_run_test_disallowed_commands(root, cmd):
    assert not_allowed(tools.run_test(cmd, root=root))


def test_pytest_paths_must_be_in_sandbox(root):
    assert refused(tools.run_test("python3 -m pytest ../secret.txt", root=root))
    assert refused(tools.run_test("python3 -m pytest /etc", root=root))
    assert not_allowed(tools.run_test("python3 -m pytest --version sandbox/app", root=root))


def test_pytest_argv_is_jailed_and_paths_rewritten(root, monkeypatch):
    seen = {}

    class P:
        returncode, stdout, stderr = 0, "", ""

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return P()

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    r = tools.run_test("python3 -m pytest sandbox/app", root=root)
    assert r["ok"] and r["passed"]
    argv = seen["argv"]
    assert argv[0].endswith("bwrap") and "--unshare-all" in argv and "--die-with-parent" in argv
    assert argv[-3:] == ["no:cacheprovider", "sandbox/app"][-3:] or argv[-1] == "sandbox/app"


def test_snippet_cannot_read_outside_sandbox(root):
    canary = root / "canary.txt"
    canary.write_text("CANARY-VALUE")
    home_rc = os.path.expanduser("~/.bashrc")
    env_file = str(tools.PROJECT_ROOT / ".env")
    snippet = (
        "import sys\n"
        f"for p in [{str(canary)!r}, {home_rc!r}, {env_file!r}]:\n"
        "    try:\n"
        "        open(p).read(); print('LEAK', p)\n"
        "    except OSError:\n"
        "        pass\n")
    r = tools.run_test(f"python3 -c {shlex.quote(snippet)}", root=root)
    assert r["ok"] and "LEAK" not in r["stdout"] and "CANARY" not in r["stdout"], r


def test_snippet_cannot_write_outside_or_inside_sandbox(root):
    outside = root / "written.txt"
    snippet = (
        "for p in [%r, 'sandbox/app/new.txt', 'sandbox/app/a.py']:\n"
        "    try:\n"
        "        open(p, 'w').write('x'); print('WROTE', p)\n"
        "    except OSError:\n"
        "        pass\n" % str(outside))
    r = tools.run_test(f"python3 -c {shlex.quote(snippet)}", root=root)
    assert r["ok"] and "WROTE" not in r["stdout"], r
    assert not outside.exists()
    assert not (root / "sandbox/app/new.txt").exists()
    assert (root / "sandbox/app/a.py").read_text() == "x = 1\n"


def test_network_blocked(root):
    snippet = (
        "import socket\n"
        "s = socket.socket(); s.settimeout(3)\n"
        "try:\n"
        "    s.connect(('1.1.1.1', 80)); print('CONNECTED')\n"
        "except OSError:\n"
        "    print('BLOCKED')\n")
    r = tools.run_test(f"python3 -c {shlex.quote(snippet)}", root=root)
    assert r["ok"] and "BLOCKED" in r["stdout"] and "CONNECTED" not in r["stdout"], r


def test_bwrap_missing_fails_closed(root, monkeypatch):
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    r = tools.run_test("python3 -c 'print(1)'", root=root)
    assert r["ok"] is False and r["error_code"] == "BWRAP_UNAVAILABLE"


def _fixture_test(name):
    path = tools.PROJECT_ROOT / "evals" / "fixtures" / "plan" / name
    return json.loads(path.read_text())


def _find_test_field(obj):
    if isinstance(obj, dict):
        if isinstance(obj.get("test"), str):
            return obj["test"]
        for v in obj.values():
            found = _find_test_field(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_test_field(v)
            if found:
                return found
    return None


@pytest.mark.parametrize("name", ["impl-calc.json", "impl-storage.json"])
def test_stage4_fixture_commands_execute_in_jail(name):
    cmd = _find_test_field(_fixture_test(name))
    assert cmd
    r = tools.run_test(cmd)
    assert r["ok"] is True and isinstance(r["passed"], bool) and "exit_code" in r, r


def test_real_sandbox_plan_command_runs():
    cmd = ("python3 -c \"import sys; sys.path.insert(0, 'sandbox/demo-app'); "
           "import calc; calc.average\"")
    r = tools.run_test(cmd)
    assert r["ok"] and r["passed"], r
