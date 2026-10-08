"""Slash commands: deterministic TUI routes that never touch the model.

Spec section 4.3: /root, /reboot, /logs, /about. Unknown or non-slash
input returns False so the caller can route it to the agent.
"""

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class SlashContext:
    reboot: Callable[[], None]
    logs: Callable[[], str]
    about: str
    root_shell: Callable[[], None] | None
    reply: Callable[[str], None] | None = None  # optional display channel


def handle_slash(line: str, ctx: SlashContext) -> bool:
    """Handle a slash command; return True if it was one."""
    text = line.strip()
    if not text.startswith("/"):
        return False
    command = text.split()[0]

    if command == "/reboot":
        ctx.reboot()
        return True
    if command == "/logs":
        output = ctx.logs()
        if ctx.reply is not None:
            ctx.reply(output)
        return True
    if command == "/about":
        if ctx.reply is not None:
            ctx.reply(ctx.about)
        return True
    if command == "/root":
        if ctx.root_shell is None:
            if ctx.reply is not None:
                ctx.reply("/root is unavailable on the host; it exists on O.")
        else:
            ctx.root_shell()
        return True
    return False
