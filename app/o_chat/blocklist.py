"""Static blocklist of catastrophic commands.

Checked before user confirmation: these are refused even when the user
says yes. Spec section 5.4: rm -rf /, mkfs, dd to raw block devices,
fork bombs, chmod -R /.
"""

import re

_ROOT_TARGET = r"(?:/\*|/\.\*?|/)\s*$"

BLOCKED_PATTERNS: list[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"^rm\s+(?:-\w+\s+)*-\w*r\w*(?:\s+-\w+r\w*)*\s+"
            r"(?:-\w+\s+)*" + _ROOT_TARGET
        ),
        "recursive delete on the system root",
    ),
    (
        re.compile(r"^mkfs(?:\.\w+)?\b"),
        "filesystem creation",
    ),
    (
        re.compile(r"^dd\b.*\bof=/dev/(?:sd|hd|nvme|vd|mmcblk)"),
        "raw write to a block device",
    ),
    (
        re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
        "fork bomb",
    ),
    (
        re.compile(
            r"^ch(?:mod|own)\s+(?:-\w+\s+)*-\w*[rR]\w*\s+\S+\s+" + _ROOT_TARGET
        ),
        "recursive permission/ownership change on the system root",
    ),
]


def check(cmd: str) -> str | None:
    """Return a human-readable reason if cmd is blocked, else None."""
    stripped = cmd.strip()
    for pattern, reason in BLOCKED_PATTERNS:
        if pattern.search(stripped):
            return reason
    return None
