"""Blocklist: refuse catastrophic commands even when the user confirms."""

import pytest

from o_chat.blocklist import check


@pytest.mark.parametrize("cmd", [
    "rm -rf /",
    "rm -fr /",
    "rm -rf /*",
    "mkfs.ext4 /dev/sda1",
    "dd if=a of=/dev/sdb",
    "dd if=a of=/dev/nvme0n1",
    ":(){ :|:& };:",
    "chmod -R 777 /",
])
def test_blocked(cmd):
    assert check(cmd) is not None


@pytest.mark.parametrize("cmd", [
    "rm -rf ~/build",
    "dd if=a of=disk.img",
    "chmod -R 755 ./src",
    "o-pkg install htop",
])
def test_allowed(cmd):
    assert check(cmd) is None


@pytest.mark.parametrize("cmd", [
    # chained / prefixed / wrapped variants (review C-1 repros)
    "echo hi; rm -rf /",
    "echo hi && rm -rf /",
    "echo pwned && mkfs.ext4 /dev/sda1",
    "doas rm -rf /",
    "sudo rm -rf /",
    "sh -c 'rm -rf /'",
    "rm --recursive -f /",
    "f(){ f|f& };f",
    "dd if=a of=/dev/disk2",
    "dd if=a of=/dev/loop0",
])
def test_blocked_evasion_variants(cmd):
    assert check(cmd) is not None


@pytest.mark.parametrize("cmd", [
    "rm -rf ~/build",
    "dd if=a of=disk.img",
    "chmod -R 755 ./src",
    "o-pkg install htop",
    "echo rm -rf / is dangerous",
])
def test_allowed_evasion_variants(cmd):
    assert check(cmd) is None


def test_blocked_reason_is_human_readable():
    reason = check("rm -rf /")
    assert isinstance(reason, str) and len(reason) > 0
