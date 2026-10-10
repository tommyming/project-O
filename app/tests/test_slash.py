"""Slash commands: deterministic TUI-handled routes, never through the model."""

from o_chat.slash import SlashContext, handle_slash


def make_ctx(**kw):
    calls = []
    kw.setdefault("reboot", lambda: calls.append("reboot"))
    kw.setdefault("logs", lambda: "log-line-1\nlog-line-2")
    kw.setdefault("about", "O 0.1 — prototype")
    kw.setdefault("root_shell", lambda: calls.append("root"))
    ctx = SlashContext(**kw)
    return ctx, calls


def test_known_commands_dispatch():
    for line, expected in [("/reboot", "reboot"), ("/root", "root")]:
        ctx, calls = make_ctx()
        assert handle_slash(line, ctx) is True
        assert expected in calls


def test_logs_dispatches():
    ctx, calls = make_ctx()
    assert handle_slash("/logs", ctx) is True


def test_about_dispatches():
    ctx, calls = make_ctx()
    assert handle_slash("/about", ctx) is True


def test_unknown_slash_returns_false():
    ctx, _ = make_ctx()
    assert handle_slash("/nonsense", ctx) is False


def test_non_slash_input_returns_false():
    ctx, calls = make_ctx()
    assert handle_slash("hello", ctx) is False
    assert handle_slash("", ctx) is False
    assert calls == []


def test_root_shell_none_reports_unavailable_without_raising():
    ctx, _ = make_ctx(root_shell=None)
    assert handle_slash("/root", ctx) is True  # handled, but reports unavailable


def test_no_root_shell_does_not_raise_on_other_commands():
    ctx, _ = make_ctx(root_shell=None)
    assert handle_slash("/reboot", ctx) is True
