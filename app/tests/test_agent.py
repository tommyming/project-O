"""AgentLoop: the confirm-then-execute state machine over streamed events."""

import pytest

from o_chat.agent import (
    SYSTEM_PROMPT_TEMPLATE,
    AgentLoop,
    Limits,
    ProposalShown,
    TextDelta,
    ToolOutcome,
    TurnComplete,
)
from o_chat.executor import ExecResult
from o_chat.model_client import LlamaClient
from mock_server import SSE_TEXT, SSE_TOOL, SSE_MIXED, MockLlamaServer

VALID_INSTALL = SSE_TOOL("install_packages", {"names": ["htop"]})


class FakeToolRunner:
    """Programs ExecResults FIFO; records every (name, args) it received."""

    def __init__(self, results: list[ExecResult] | None = None):
        self.results = list(results or [])
        self.calls: list[tuple[str, dict]] = []

    def execute(self, name: str, args: dict) -> ExecResult:
        self.calls.append((name, args))
        if self.results:
            return self.results.pop(0)
        return ExecResult(0, "ok", "")


def run_turn(script, runner, limits=Limits()):
    with MockLlamaServer(script) as mock:
        loop = AgentLoop(LlamaClient(mock.url), runner, "You are O.", limits)
        events = []
        messages = loop.turn("please install htop", events.append, [])
        return events, messages, mock


def reasons(events):
    return [e.reason for e in events if isinstance(e, TurnComplete)]


def test_happy_path_install():
    runner = FakeToolRunner(results=[ExecResult(0, "installed htop", "")])
    events, messages, _ = run_turn(
        [VALID_INSTALL, SSE_TEXT("Installed htop for you.")], runner
    )
    assert reasons(events) == ["done"]
    assert any(isinstance(e, ProposalShown) for e in events)
    outcomes = [e for e in events if isinstance(e, ToolOutcome)]
    assert len(outcomes) == 1
    assert runner.calls == [("install_packages", {"names": ["htop"]})]
    assert any(m.get("role") == "tool" for m in messages)
    assert any(
        isinstance(e, TextDelta) and "Installed htop" in e.text for e in events
    )


def test_happy_path_proposal_privileged():
    runner = FakeToolRunner()
    events, _, _ = run_turn([VALID_INSTALL, SSE_TEXT("done")], runner)
    proposal = next(e.proposal for e in events if isinstance(e, ProposalShown))
    assert proposal.privileged is True
    assert proposal.cmd == "o-pkg install htop"


def test_run_command_privilege_from_args():
    runner = FakeToolRunner()
    script = [
        SSE_TOOL("run_command",
                 {"cmd": "ls /etc", "explanation": "list config", "privileged": True}),
        SSE_TEXT("here"),
    ]
    events, _, _ = run_turn(script, runner)
    proposal = next(e.proposal for e in events if isinstance(e, ProposalShown))
    assert proposal.privileged is True
    assert proposal.cmd == "ls /etc"


def test_declined_path_replans_without_second_execution():
    runner = FakeToolRunner(
        results=[ExecResult(-1, "", "user declined", declined=True)]
    )
    events, messages, _ = run_turn(
        [VALID_INSTALL, SSE_TEXT("Okay, I won't install it.")], runner
    )
    assert reasons(events) == ["done"]
    outcomes = [e for e in events if isinstance(e, ToolOutcome)]
    assert len(outcomes) == 1 and outcomes[0].result.declined is True
    assert len(runner.calls) == 1


def test_malformed_then_valid():
    runner = FakeToolRunner()
    script = [SSE_TOOL("delete_everything", {}), VALID_INSTALL, SSE_TEXT("done")]
    events, _, mock = run_turn(script, runner)
    assert reasons(events) == ["done"]
    assert runner.calls == [("install_packages", {"names": ["htop"]})]
    # The retry request fed the validation error back to the model...
    retry_msg = mock.requests[1]["messages"][-1]["content"]
    assert "unknown" in retry_msg or "ToolValidationError" in retry_msg
    # ...and was grammar-constrained per spec 5.4; the follow-up was not.
    assert "grammar" in mock.requests[1]
    assert "grammar" not in mock.requests[2]


def test_malformed_twice_falls_back():
    runner = FakeToolRunner()
    script = [SSE_TOOL("nope_a", {}), SSE_TOOL("nope_b", {})]
    events, _, _ = run_turn(script, runner)
    assert reasons(events) == ["invalid_tool"]
    assert any(
        isinstance(e, TextDelta) and "couldn't do that" in e.text for e in events
    )
    assert runner.calls == []


def test_mixed_turn_emits_text_and_gates_tool():
    runner = FakeToolRunner()
    events, _, _ = run_turn(
        [SSE_MIXED("Let me install that.", "install_packages", {"names": ["jq"]}),
         SSE_TEXT("Done.")],
        runner,
    )
    assert any(
        isinstance(e, TextDelta) and "Let me install that." in e.text for e in events
    )
    assert any(isinstance(e, ProposalShown) for e in events)
    assert any(isinstance(e, ToolOutcome) for e in events)
    assert runner.calls == [("install_packages", {"names": ["jq"]})]


def test_tool_call_limit():
    runner = FakeToolRunner()
    script = [SSE_TOOL("sys_info", {}, call_id=f"c{i}") for i in range(9)]
    events, _, _ = run_turn(script, runner)
    assert reasons(events) == ["limit"]
    outcomes = [e for e in events if isinstance(e, ToolOutcome)]
    assert len(outcomes) == 8
    assert len(runner.calls) == 8


def test_system_prompt_template():
    prompt = SYSTEM_PROMPT_TEMPLATE.format(os_name="O", username="tommy")
    assert "O" in prompt and "tommy" in prompt
    for tool in ("run_command", "search_packages", "install_packages",
                 "remove_packages", "list_installed", "sys_info"):
        assert tool in prompt
    assert "repository" in prompt  # never invent packages rule


def test_turn_returns_new_entries_only():
    prior = [{"role": "assistant", "content": "earlier"}]
    runner = FakeToolRunner()
    _, messages, _ = run_turn([SSE_TEXT("hi")], runner, limits=Limits())
    with MockLlamaServer([SSE_TEXT("hi")]) as mock:
        loop = AgentLoop(LlamaClient(mock.url), runner, "You are O.")
        out = loop.turn("hello", lambda e: None, prior)
    assert out[0] == {"role": "user", "content": "hello"}
    assert prior == [{"role": "assistant", "content": "earlier"}]
