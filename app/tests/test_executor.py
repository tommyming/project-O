"""Executor: blocklist -> confirmation -> subprocess with limits."""

import stat

from o_chat.executor import ExecResult, Executor, Proposal


def make_proposal(cmd, **kw):
    kw.setdefault("explanation", "test command")
    kw.setdefault("tool", "run_command")
    kw.setdefault("args", {})
    return Proposal(cmd=cmd, **kw)


def test_decline_never_spawns(tmp_path):
    marker = tmp_path / "marker"
    ex = Executor(confirm=lambda p: False)
    r = ex.run(make_proposal(f"touch {marker}"))
    assert r.declined is True
    assert not marker.exists()
    assert "declined" in r.stderr


def test_confirmed_echo_captures_stdout():
    ex = Executor(confirm=lambda p: True)
    r = ex.run(make_proposal("echo hi"))
    assert r.exit_code == 0
    assert r.stdout.strip() == "hi"
    assert r.declined is False and r.timed_out is False


def test_command_failure_captures_stderr():
    ex = Executor(confirm=lambda p: True)
    r = ex.run(make_proposal("echo oops >&2; exit 3"))
    assert r.exit_code == 3
    assert "oops" in r.stderr


def test_timeout_kills(tmp_path):
    ex = Executor(confirm=lambda p: True, timeout=1)
    r = ex.run(make_proposal("sleep 5"))
    assert r.timed_out is True


def test_output_truncated_to_limit():
    ex = Executor(confirm=lambda p: True, timeout=10, max_output=4096)
    r = ex.run(make_proposal("yes A | head -c 9000"))
    assert len(r.stdout) <= 4096
    assert r.truncated is True


def test_blocklist_beats_confirmation(tmp_path, monkeypatch):
    ex = Executor(confirm=lambda p: True)
    r = ex.run(Proposal(cmd="rm -rf /", explanation="x", tool="run_command",
                        args={}, privileged=False))
    assert r.exit_code == 126 and "blocked" in r.stderr and not r.declined


def _install_doas_shim(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "doas.log"
    shim = bin_dir / "doas"
    shim.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$*" >> {log}\n'
        'exec "$@"\n'
    )
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    return log


def test_privileged_prefixes_doas(tmp_path, monkeypatch):
    log = _install_doas_shim(tmp_path, monkeypatch)
    ex = Executor(confirm=lambda p: True)
    r = ex.run(make_proposal("echo privileged", privileged=True))
    assert r.exit_code == 0
    assert "privileged" in r.stdout
    assert log.exists()
    assert "/bin/sh" in log.read_text()


def test_unprivileged_skips_doas(tmp_path, monkeypatch):
    log = _install_doas_shim(tmp_path, monkeypatch)
    ex = Executor(confirm=lambda p: True)
    r = ex.run(make_proposal("echo plain", privileged=False))
    assert r.exit_code == 0
    assert not log.exists()
