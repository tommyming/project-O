"""The agent loop: streamed model turns driving confirm-then-execute tools.

Spec section 5.3. The loop owns nothing: history comes in as a list and
the new entries come out; the caller persists them.
"""

import json
import platform
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from o_chat.executor import ExecResult, Executor, Proposal
from o_chat.model_client import LlamaClient
from o_chat.tools import (
    ToolValidationError,
    tool_call_grammar,
    tool_schemas,
    validate_tool_call,
)

FALLBACK_TEXT = "I'm sorry — I couldn't do that reliably."

SYSTEM_PROMPT_TEMPLATE = """You are the personal assistant of {os_name}, an operating system that is nothing but this conversation. You are talking with {username}, the sole user of this machine.

You manage software through tools. Available tools: run_command, search_packages, install_packages, remove_packages, list_installed, sys_info.

Rules:
- Propose exactly one tool call at a time, and explain in plain words what it will do before proposing it.
- Software comes from the local package repository only. Never claim a package exists if it is not in the repository; if a search finds nothing, say so honestly.
- After a tool result, briefly tell the user what happened.
- Be concise. The user is not necessarily technical.
"""


@dataclass
class Limits:
    max_tool_calls: int = 8
    max_retries: int = 2


@dataclass
class TextDelta:
    text: str


@dataclass
class ProposalShown:
    proposal: Proposal


@dataclass
class ToolOutcome:
    proposal: Proposal
    result: ExecResult


@dataclass
class TurnComplete:
    reason: str  # "done" | "invalid_tool" | "limit"


def tool_to_proposal(name: str, args: dict[str, Any]) -> Proposal:
    """Map a validated tool call to the Proposal shown and executed."""
    if name == "run_command":
        return Proposal(
            cmd=args["cmd"],
            explanation=args.get("explanation", ""),
            tool=name,
            args=args,
            privileged=bool(args.get("privileged", False)),
        )
    if name in ("install_packages", "remove_packages"):
        names = args.get("names", [])
        quoted = " ".join(shlex.quote(n) for n in names)
        verb = "install" if name == "install_packages" else "remove"
        return Proposal(
            cmd=f"o-pkg {verb} {quoted}".rstrip(),
            explanation=f"{verb} {', '.join(names)}",
            tool=name,
            args=args,
            privileged=True,
        )
    # Read-only tools are answered locally; never executed as commands.
    return Proposal(cmd="", explanation=name, tool=name, args=args, privileged=False)


class ToolRunner:
    """Executes tool calls: read-only tools locally, actions via Executor."""

    def __init__(
        self,
        executor: Executor,
        repo_index: Path | None = None,
        installed_ledger: Path | None = None,
    ):
        self._executor = executor
        self._repo_index = Path(repo_index) if repo_index else None
        self._ledger = Path(installed_ledger) if installed_ledger else None

    def execute(self, name: str, args: dict[str, Any]) -> ExecResult:
        if name == "search_packages":
            return ExecResult(0, json.dumps(self._search(args.get("query", ""))), "")
        if name == "list_installed":
            return ExecResult(0, json.dumps(self._installed()), "")
        if name == "sys_info":
            return ExecResult(0, self._sys_info(), "")
        return self._executor.run(tool_to_proposal(name, args))

    def _search(self, query: str) -> list[dict]:
        if not self._repo_index or not self._repo_index.is_file():
            return []
        try:
            packages = json.loads(self._repo_index.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        q = query.lower()
        return [
            p
            for p in packages
            if q in str(p.get("name", "")).lower()
            or q in str(p.get("description", "")).lower()
        ]

    def _installed(self) -> list:
        if not self._ledger or not self._ledger.is_file():
            return []
        try:
            return json.loads(self._ledger.read_text())
        except (json.JSONDecodeError, OSError):
            return []

    def _sys_info(self) -> str:
        usage = shutil.disk_usage("/")
        try:
            with open("/proc/uptime") as f:
                uptime_seconds = float(f.read().split()[0])
            uptime = f"{int(uptime_seconds // 3600)}h{int(uptime_seconds % 3600 // 60)}m"
        except OSError:
            uptime = "unknown"
        return (
            f"os=O kernel={platform.system()} {platform.release()} "
            f"arch={platform.machine()} "
            f"disk_free={usage.free // (1024 * 1024)}MiB uptime={uptime}"
        )


class AgentLoop:
    def __init__(
        self,
        client: LlamaClient,
        runner: ToolRunner,
        system_prompt: str,
        limits: Limits = Limits(),
    ):
        self._client = client
        self._runner = runner
        self._system_msg = {"role": "system", "content": system_prompt}
        self._limits = limits

    def turn(
        self,
        user_message: str,
        emit: Callable[[Any], None],
        messages: list[dict],
    ) -> list[dict]:
        """Run one user turn; return the new history entries it produced."""
        history = [*messages, {"role": "user", "content": user_message}]
        new_entries = [history[-1]]

        tool_calls_used = 0
        retries_used = 0
        grammar: str | None = None
        call_seq = 0

        while True:
            text_parts: list[str] = []
            tool_acc: dict[int, dict] = {}

            for event in self._client.chat(
                [self._system_msg, *history],
                tools=tool_schemas(),
                grammar=grammar,
            ):
                if event.text:
                    text_parts.append(event.text)
                    emit(TextDelta(event.text))
                if event.tool_calls:
                    for tc in event.tool_calls:
                        idx = tc.get("index", 0)
                        slot = tool_acc.setdefault(
                            idx, {"id": None, "name": "", "arguments": ""}
                        )
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        slot["arguments"] += fn.get("arguments") or ""

            text = "".join(text_parts)

            call = None
            if tool_acc:
                # Spec: one tool call at a time; take the first slot.
                call = next(iter(tool_acc.values()))
            elif grammar and text.strip():
                # Grammar-constrained retry: the whole completion is forced
                # to bare JSON and arrives as content. Recognize it.
                try:
                    candidate = json.loads(text)
                    if isinstance(candidate, dict) and isinstance(
                        candidate.get("name"), str
                    ):
                        call = {
                            "id": None,
                            "name": candidate["name"],
                            "arguments": json.dumps(
                                candidate.get("arguments", {})
                            ),
                        }
                except json.JSONDecodeError:
                    call = None

            if call is None:
                entry = {"role": "assistant", "content": text}
                history.append(entry)
                new_entries.append(entry)
                emit(TurnComplete("done"))
                return new_entries
            call_id = call["id"] or f"call_{call_seq}"
            call_seq += 1
            assistant_entry = {
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": call["arguments"] or "{}",
                        },
                    }
                ],
            }

            try:
                normalized: dict[str, Any] = {"name": call["name"]}
                try:
                    normalized["arguments"] = json.loads(call["arguments"] or "{}")
                except json.JSONDecodeError:
                    raise ToolValidationError(
                        "tool call arguments are not valid JSON"
                    )
                name, args = validate_tool_call(normalized)
            except ToolValidationError as e:
                if retries_used >= self._limits.max_retries:
                    emit(TextDelta(FALLBACK_TEXT))
                    emit(TurnComplete("invalid_tool"))
                    return new_entries
                retries_used += 1
                history.append(assistant_entry)
                new_entries.append(assistant_entry)
                error_entry = {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": f"error: {e}",
                }
                history.append(error_entry)
                new_entries.append(error_entry)
                grammar = tool_call_grammar()
                continue

            if tool_calls_used >= self._limits.max_tool_calls:
                emit(TurnComplete("limit"))
                return new_entries
            tool_calls_used += 1
            grammar = None

            proposal = tool_to_proposal(name, args)
            emit(ProposalShown(proposal))
            result = self._runner.execute(name, args)
            emit(ToolOutcome(proposal, result))

            history.append(assistant_entry)
            new_entries.append(assistant_entry)
            result_entry = {
                "role": "tool",
                "tool_call_id": call_id,
                "content": _render_result(result),
            }
            history.append(result_entry)
            new_entries.append(result_entry)


def _render_result(result: ExecResult) -> str:
    status = (
        f"declined" if result.declined
        else f"timed_out" if result.timed_out
        else f"exit={result.exit_code}"
    )
    text = f"{status}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    raw = text.encode("utf-8")
    if len(raw) > 4096:
        notice = "\n[output truncated]"
        cut = raw[: 4096 - len(notice)]
        return cut.decode("utf-8", "replace") + notice
    return text
