"""Static blocklist of catastrophic commands.

Checked before user confirmation: these are refused even when the user
says yes. Spec section 5.4: rm -rf /, mkfs, dd to raw block devices,
fork bombs, chmod -R /.

Defense against common shapes (review C-1): privilege prefixes
(doas/sudo/env) are stripped, `sh -c` payloads are unwrapped, and every
shell-separated segment is checked, so chained (`;`, `&&`, `|`) and
wrapped forms are caught too.
"""

import re

_ROOT_TARGETS = ("/", "/*", "/.", "/.*", "/*/")
_DEVICE_RE = re.compile(
    r"of=/dev/(?:sd|hd|nvme|vd|mmcblk|disk|rdisk|loop)\w*"
)
_FORK_BOMB = re.compile(r"(\S+)\s*\(\)\s*\{[^}]*\|[^}]*&[^}]*\}\s*;\s*\1")
_MKFS = re.compile(r"^mkfs(?:\.\w+)?\b")
_SH_C = re.compile(r"^(?:ba|z|da|k)?sh\s+(?:-{1,2}[\w=-]+\s+)*-c\s+(.+)$")
_PRIV_PREFIX = re.compile(r"^(?:doas|sudo|env)\s+(?:[A-Za-z_]\w*=\S*\s+)*(.*)$")


def check(cmd: str) -> str | None:
    """Return a human-readable reason if cmd is blocked, else None."""
    text = cmd.strip()
    for _ in range(3):  # unwrap sh -c / strip prefixes, bounded depth
        unwrapped = _unwrap(_strip_prefixes(text))
        if unwrapped == text:
            break
        text = unwrapped

    if _FORK_BOMB.search(text):
        return "fork bomb"

    for segment in re.split(r"\|\||&&|[;|&\n]", text):
        reason = _check_segment(segment.strip())
        if reason is not None:
            return reason
    return None


def _strip_prefixes(text: str) -> str:
    while True:
        m = _PRIV_PREFIX.match(text)
        if not m or m.group(1) == text:
            return text
        text = m.group(1).strip()


def _unwrap(text: str) -> str:
    m = _SH_C.match(text)
    if not m:
        return text
    payload = m.group(1).strip()
    if len(payload) >= 2 and payload[0] == payload[-1] and payload[0] in "'\"":
        payload = payload[1:-1]
    return payload


def _check_segment(segment: str) -> str | None:
    if not segment:
        return None
    if _MKFS.search(segment):
        return "filesystem creation"
    if segment.split()[:1] == ["dd"] and _DEVICE_RE.search(segment):
        return "raw write to a block device"
    if _recursive_on_root(segment, "rm"):
        return "recursive delete on the system root"
    if _recursive_on_root(segment, "chmod") or _recursive_on_root(segment, "chown"):
        return "recursive permission/ownership change on the system root"
    return None


def _recursive_on_root(segment: str, name: str) -> bool:
    tokens = segment.split()
    if not tokens or tokens[0] != name:
        return False
    recursive = False
    target = None
    for token in tokens[1:]:
        if token.startswith("-"):
            if token == "--recursive" or (not token.startswith("--")
                                          and ("r" in token or "R" in token)):
                recursive = True
        else:
            target = token
    return recursive and target is not None and (
        target in _ROOT_TARGETS or target.rstrip("*") in _ROOT_TARGETS
    )
