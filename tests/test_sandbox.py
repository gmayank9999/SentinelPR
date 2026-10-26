"""The execution sandbox, attacked from inside.

Each test writes a deliberately hostile "repository" and runs it through the real sandbox. The hostile
tests assert that the attack FAILS, so a PASSED outcome means the boundary held. They need a running
Docker engine and are skipped (reported as skipped, not passed) without one.
"""

import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from sentinel.verify import sandbox as sbx
from sentinel.verify.runner import run_tests

pytestmark = pytest.mark.skipif(not sbx.docker_status()["available"], reason="Docker engine not running")


def project(tmp_path: Path, body: str) -> Path:
    root = tmp_path / "evil"
    (root / "tests").mkdir(parents=True)
    (root / "evil").mkdir()
    (root / "evil" / "__init__.py").write_text("", encoding="utf-8")
    (root / "tests" / "test_attack.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return root


def run(root: Path, tmp_path: Path, **kw):
    spec = sbx.SandboxSpec(mode="docker", **kw.pop("spec", {}))
    return run_tests(root, ["tests/test_attack.py"], package="evil", coverage=False, timeout_s=kw.pop("timeout_s", 120), sandbox=spec, scratch=tmp_path / "scratch", **kw)


def outcomes(result):
    return {o.nodeid.split("::")[-1]: o.outcome for o in result.run.outcomes}


def test_the_attack_tests_really_ran_in_a_container(tmp_path):
    root = project(tmp_path, """
        import os
        def test_inside_container():
            assert os.path.exists("/.dockerenv") or os.environ.get("HOME") == "/tmp"
            assert os.getuid() != 0
    """)
    assert outcomes(run(root, tmp_path)) == {"test_inside_container": "passed"}


def test_no_network(tmp_path):
    root = project(tmp_path, """
        import socket, pytest
        @pytest.mark.parametrize("host", ["1.1.1.1", "8.8.8.8", "169.254.169.254"])
        def test_cannot_connect(host):
            s = socket.socket(); s.settimeout(3)
            with pytest.raises(OSError):
                s.connect((host, 80))
        def test_cannot_resolve():
            with pytest.raises(OSError):
                socket.gethostbyname("example.com")
    """)
    assert set(outcomes(run(root, tmp_path)).values()) == {"passed"}


def test_host_secrets_and_files_are_not_visible(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "x" * 36)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_" + "y" * 40)
    root = project(tmp_path, """
        import os, pathlib, pytest
        def test_no_secrets_in_environment():
            blob = repr(dict(os.environ))
            assert "ghp_" not in blob and "gsk_" not in blob and "GITHUB_TOKEN" not in os.environ and "GROQ_API_KEY" not in os.environ
        def test_no_docker_socket():
            assert not os.path.exists("/var/run/docker.sock") and not os.path.exists("/run/docker.sock")
        @pytest.mark.parametrize("path", ["/root/.ssh", "/home", "/etc/shadow", "/host", "/mnt/c", "/c"])
        def test_host_paths_absent_or_unreadable(path):
            p = pathlib.Path(path)
            try:
                if p.is_dir():
                    assert list(p.iterdir()) == []
                elif p.exists():
                    p.read_text()
                    raise AssertionError(path + " was readable")
            except PermissionError:
                pass  # access denied is the secure outcome
        def test_no_git_credentials():
            assert not pathlib.Path("/tmp/.gitconfig").exists() and not pathlib.Path("/work/.git").exists()
    """)
    assert set(outcomes(run(root, tmp_path)).values()) == {"passed"}


def test_root_filesystem_is_read_only_and_writes_stay_in_the_copy(tmp_path):
    root = project(tmp_path, """
        import pathlib, pytest
        @pytest.mark.parametrize("path", ["/usr/evil", "/etc/evil", "/opt/evil", "/plugin/evil", "/deps/evil"])
        def test_cannot_write_outside_workdir(path):
            with pytest.raises(OSError):
                pathlib.Path(path).write_text("x")
        def test_writes_in_workdir_are_local():
            pathlib.Path("/work/dropped.txt").write_text("hello")
    """)
    result = run(root, tmp_path)
    assert set(outcomes(result).values()) == {"passed"}
    assert not (root / "dropped.txt").exists(), "a test wrote into the host's copy of the repository"


def test_process_limit_stops_a_fork_bomb(tmp_path):
    root = project(tmp_path, """
        import subprocess, pytest
        def test_cannot_spawn_unbounded_processes():
            kids = []
            try:
                with pytest.raises(OSError):
                    for _ in range(2000):
                        kids.append(subprocess.Popen(["sleep", "60"]))
            finally:
                for k in kids:
                    k.kill()
            assert len(kids) < 400
    """)
    assert set(outcomes(run(root, tmp_path, spec={"pids": 64})).values()) == {"passed"}


def test_memory_limit_kills_a_memory_hog(tmp_path):
    root = project(tmp_path, """
        def test_hog():
            blocks = []
            while True:
                blocks.append(bytearray(64 * 1024 * 1024))
    """)
    result = run(root, tmp_path, spec={"memory": "192m"})
    assert "passed" not in outcomes(result).values()  # killed or MemoryError, never a pass


def test_timeout_kills_the_container(tmp_path):
    root = project(tmp_path, """
        def test_spin():
            while True:
                pass
    """)
    result = run(root, tmp_path, timeout_s=8)
    assert result.timed_out
    left = subprocess.run(["docker", "ps", "-aq", "--filter", f"label={sbx.LABEL}"], capture_output=True, text=True).stdout.split()
    assert left == [], "a timed-out job left its container running"


def test_workspace_copies_are_cleaned_up(tmp_path):
    root = project(tmp_path, "def test_ok():\n    assert True\n")
    run(root, tmp_path)
    stage = tmp_path / "sandbox-stage"
    assert not [p for p in stage.glob("sbx-*")], "temporary workspace copies were left behind"


def test_cross_run_data_does_not_leak(tmp_path):
    first = project(tmp_path / "a", """
        import pathlib
        def test_leave_a_note():
            pathlib.Path("/tmp/note.txt").write_text("secret")
            pathlib.Path("/work/note.txt").write_text("secret")
    """)
    second = project(tmp_path / "b", """
        import pathlib
        def test_nothing_from_before():
            assert not pathlib.Path("/tmp/note.txt").exists() and not pathlib.Path("/work/note.txt").exists()
    """)
    assert set(outcomes(run(first, tmp_path / "a")).values()) == {"passed"}
    assert set(outcomes(run(second, tmp_path / "b")).values()) == {"passed"}


def test_unavailable_docker_is_an_error_not_a_host_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(sbx, "_status", None)
    monkeypatch.setattr(sbx.shutil, "which", lambda _name: None)
    root = project(tmp_path, "def test_would_run_on_host():\n    open('/tmp/ran_on_host', 'w').write('x')\n")
    with pytest.raises(sbx.SandboxError) as err:
        run(root, tmp_path)
    assert err.value.code == "sandbox_unavailable" and "will not fall back" in str(err.value)
    assert not os.path.exists("/tmp/ran_on_host")
    monkeypatch.setattr(sbx, "_status", None)  # let later tests see the real engine again
