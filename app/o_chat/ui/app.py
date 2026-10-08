"""ChatApp: the chat window that is the entire user experience of O.

Runs one agent turn per submitted input line in a Textual worker
thread; Esc cancels the in-flight turn; a dead model server degrades
to a "brain offline" status without killing the app (spec section 8).
"""

import getpass
from functools import partial

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Input, RichLog, Static

from o_chat.agent import (
    SYSTEM_PROMPT_TEMPLATE,
    AgentLoop,
    ProposalShown,
    TextDelta,
    ToolOutcome,
    TurnComplete,
)
from o_chat.config import load_config
from o_chat.executor import Executor
from o_chat.history import History
from o_chat.model_client import LlamaClient, LlamaServerError
from o_chat.agent import ToolRunner


class _TurnCancelled(Exception):
    pass


class ChatApp(App):
    CSS = """
    #log { height: 1fr; }
    #input { dock: bottom; }
    #status { dock: bottom; height: 1; background: $panel; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, agent: AgentLoop, history: History, status: str = ""):
        super().__init__()
        self.agent = agent
        self.history = history
        self._initial_status = status
        self.cancelled = False

    def compose(self) -> ComposeResult:
        yield RichLog(id="log", markup=False, wrap=True)
        yield Input(id="input", placeholder="type here…")
        yield Static(self._initial_status, id="status")

    def on_mount(self) -> None:
        self.query_one("#input", Input).focus()

    def action_cancel(self) -> None:
        self.cancelled = True

    # ------------------------------------------------------------------
    # Turn execution (worker thread)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        self.cancelled = False
        self._set_status("")
        self.run_worker(partial(self._run_turn, text), thread=True, exclusive=True)

    def _run_turn(self, text: str) -> None:
        try:
            new_entries = self.agent.turn(
                text, self._emit, self.history.messages()
            )
        except _TurnCancelled:
            self.call_from_thread(self._set_status, "cancelled")
            return
        except LlamaServerError as e:
            self.call_from_thread(
                self._set_status, f"brain offline — model server unreachable ({e})"
            )
            return
        except Exception as e:  # worker guard: never kill the chat window
            self.call_from_thread(self._set_status, f"error: {e}")
            return
        for entry in new_entries:
            self.history.append(
                role=entry["role"],
                content=entry.get("content"),
                tool_calls=entry.get("tool_calls"),
                tool_call_id=entry.get("tool_call_id"),
            )
        self.call_from_thread(self._set_status, "")

    # ------------------------------------------------------------------
    # Event sink (called from the agent loop inside the worker thread)

    def _emit(self, event) -> None:
        if self.cancelled:
            raise _TurnCancelled
        if isinstance(event, TextDelta):
            self.call_from_thread(self._write_log, event.text)
        elif isinstance(event, ProposalShown):
            p = event.proposal
            badge = "PRIVILEGED " if p.privileged else ""
            self.call_from_thread(
                self._write_log,
                f"[propose] {badge}{p.cmd or p.tool} — {p.explanation}",
            )
        elif isinstance(event, ToolOutcome):
            r = event.result
            state = (
                "declined" if r.declined
                else "timed out" if r.timed_out
                else f"exit {r.exit_code}"
            )
            self.call_from_thread(
                self._write_log, f"[result: {state}] {r.stdout.strip()[:200]}"
            )
        elif isinstance(event, TurnComplete):
            self.call_from_thread(
                self._set_status, "" if event.reason == "done" else event.reason
            )

    # ------------------------------------------------------------------
    # Widget helpers (main thread only, via call_from_thread)

    def _write_log(self, text: str) -> None:
        self.query_one("#log", RichLog).write(text)

    def _set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)


def main() -> None:
    cfg = load_config()
    client = LlamaClient(cfg.server_url)

    def confirm(proposal) -> bool:
        # Placeholder: deny until Task 9 wires the real ConfirmCard flow.
        return False

    executor = Executor(confirm=confirm)
    runner = ToolRunner(executor)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        os_name="O", username=getpass.getuser()
    )
    agent = AgentLoop(client, runner, system_prompt)
    history = History(cfg.history_path)
    ChatApp(agent, history, status=f"model: {cfg.model_name}").run()
