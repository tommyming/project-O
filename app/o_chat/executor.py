"""Confirmation-gated command execution.

Order is fixed (spec section 5.3): blocklist -> user confirmation ->
subprocess. The blocklist is a hard floor: blocked commands are refused
even when the user confirms.
"""

import subprocess
from dataclasses import dataclass
from typing import Callable

from o_chat.blocklist import check


@dataclass
class Proposal:
    cmd: str
    explanation: str
    tool: str
    args: dict
    privileged: bool = False


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    declined: bool = False
    truncated: bool = False


class Executor:
    def __init__(
        self,
        confirm: Callable[[Proposal], bool],
        timeout: int = 60,
        max_output: int = 4096,
        doas_bin: str | None = "doas",
    ):
        self._confirm = confirm
        self.timeout = timeout
        self.max_output = max_output
        self.doas_bin = doas_bin

    def run(self, p: Proposal) -> ExecResult:
        reason = check(p.cmd)
        if reason is not None:
            return ExecResult(126, "", f"blocked: {reason}")

        if not self._confirm(p):
            return ExecResult(-1, "", "user declined", declined=True)

        argv = ["/bin/sh", "-c", p.cmd]
        if p.privileged and self.doas_bin:
            argv = [self.doas_bin, *argv]

        try:
            proc = subprocess.run(
                argv, capture_output=True, timeout=self.timeout
            )
        except subprocess.TimeoutExpired as e:
            out = e.stdout.decode("utf-8", "replace") if e.stdout else ""
            err = e.stderr.decode("utf-8", "replace") if e.stderr else ""
            return ExecResult(-1, out, err, timed_out=True)

        out, out_trunc = self._truncate(proc.stdout)
        err, err_trunc = self._truncate(proc.stderr)
        return ExecResult(
            proc.returncode, out, err, truncated=out_trunc or err_trunc
        )

    def _truncate(self, raw: bytes) -> tuple[str, bool]:
        text = raw.decode("utf-8", "replace")
        if len(text) > self.max_output:
            return text[: self.max_output], True
        return text, False
