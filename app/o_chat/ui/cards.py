"""ConfirmCard: the y/n card every proposed command must pass through."""

from typing import Callable

from textual.widgets import Static

from o_chat.executor import Proposal


class ConfirmCard(Static):
    """Renders a proposal; y approves, n declines. One decision only."""

    def __init__(self, proposal: Proposal, on_decision: Callable[[bool], None]):
        self.proposal = proposal
        self._on_decision = on_decision
        self._decided = False
        super().__init__(self._render_card())
        self.can_focus = True

    def _render_card(self) -> str:
        badge = "PRIVILEGED " if self.proposal.privileged else ""
        cmd = self.proposal.cmd or self.proposal.tool
        return (
            f"┌─ confirm ─────────────────────────────\n"
            f"│ {badge}{cmd}\n"
            f"│ {self.proposal.explanation}\n"
            f"└─ [y] run   [n] decline"
        )

    def on_key(self, event) -> None:
        if self._decided:
            return
        if event.key == "y":
            self._decide(True)
        elif event.key == "n":
            self._decide(False)
        else:
            return
        event.prevent_default()
        event.stop()

    def _decide(self, approved: bool) -> None:
        if self._decided:
            return
        self._decided = True
        self._on_decision(approved)
        self.update(
            f"[{'approved' if approved else 'declined'}] "
            f"{self.proposal.cmd or self.proposal.tool}"
        )
        self.can_focus = False
