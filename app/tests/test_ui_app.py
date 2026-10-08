"""ChatApp: the Textual chat window (streaming, Esc-cancel, offline state)."""

import threading

import pytest
from textual.app import App
from textual.widgets import Input, RichLog, Static

from o_chat.agent import TextDelta, TurnComplete
from o_chat.history import History
from o_chat.model_client import LlamaServerError

from o_chat.ui.app import ChatApp


class FakeAgent:
    """Synchronous stand-in for AgentLoop; records calls, plays events."""

    def __init__(self, script=None, error=None, gate=None):
        self.script = list(script or [])
        self.error = error
        self.gate = gate
        self.calls: list[tuple[str, list]] = []

    def turn(self, user_message, emit, messages):
        self.calls.append((user_message, messages))
        if self.gate is not None:
            self.gate.wait(timeout=10)
        for event in self.script:
            emit(event)
        if self.error is not None:
            raise self.error
        return [*messages, {"role": "user", "content": user_message}]


def log_text(app) -> str:
    log = app.query_one("#log", RichLog)
    return "".join(getattr(strip, "text", "") for strip in log.lines)


async def test_composes_log_input_status(tmp_path):
    app = ChatApp(FakeAgent(), History(tmp_path / "h.jsonl"))
    async with app.run_test() as pilot:
        assert isinstance(app.query_one("#log", RichLog), RichLog)
        assert isinstance(app.query_one("#input", Input), Input)
        assert isinstance(app.query_one("#status", Static), Static)


async def test_agent_events_render_in_log(tmp_path):
    agent = FakeAgent(script=[TextDelta("hel"), TextDelta("lo"),
                              TurnComplete("done")])
    app = ChatApp(agent, History(tmp_path / "h.jsonl"))
    async with app.run_test() as pilot:
        app.query_one("#input", Input).value = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        assert "hello" in log_text(app)


async def test_enter_invokes_agent_turn_with_history(tmp_path):
    history = History(tmp_path / "h.jsonl")
    history.append(role="user", content="earlier")
    agent = FakeAgent(script=[TurnComplete("done")])
    app = ChatApp(agent, history)
    async with app.run_test() as pilot:
        app.query_one("#input", Input).value = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        assert len(agent.calls) == 1
        user_message, messages = agent.calls[0]
        assert user_message == "hi"
        assert messages == [{"role": "user", "content": "earlier"}]


async def test_esc_during_turn_sets_cancelled(tmp_path):
    gate = threading.Event()
    agent = FakeAgent(script=[TurnComplete("done")], gate=gate)
    app = ChatApp(agent, History(tmp_path / "h.jsonl"))
    async with app.run_test() as pilot:
        app.query_one("#input", Input).value = "go"
        await pilot.press("enter")
        await pilot.pause()
        assert app.cancelled is False
        await pilot.press("escape")
        assert app.cancelled is True
        gate.set()
        await app.workers.wait_for_complete()


async def test_server_error_shows_offline_and_app_survives(tmp_path):
    agent = FakeAgent(error=LlamaServerError("connection refused"))
    app = ChatApp(agent, History(tmp_path / "h.jsonl"))
    async with app.run_test() as pilot:
        app.query_one("#input", Input).value = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        status_text = str(app.query_one("#status", Static).content)
        assert "offline" in status_text.lower()
        # app is still alive and usable
        assert app.query_one("#input", Input) is not None
        app.query_one("#input", Input).value = "again"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        assert len(agent.calls) == 2
