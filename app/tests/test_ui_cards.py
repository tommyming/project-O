"""ConfirmCard: y/n gating rendered as a distinct card with a privilege badge."""

from textual.app import App, ComposeResult
from textual.widgets import Static

from o_chat.agent import ProposalShown
from o_chat.executor import Proposal
from o_chat.history import History
from o_chat.ui.app import ChatApp
from o_chat.ui.cards import ConfirmCard

from test_ui_app import FakeAgent  # reuse the synchronous fake


class CardApp(App):
    def compose(self) -> ComposeResult:
        yield Static("", id="out")


def mount_card(proposal) -> tuple[CardApp, ConfirmCard, list]:
    decisions: list[bool] = []
    card = ConfirmCard(proposal, decisions.append)
    return CardApp(), card, decisions


PRIV = Proposal(cmd="o-pkg install htop", explanation="install htop",
                tool="install_packages", args={}, privileged=True)
PLAIN = Proposal(cmd="ls /etc", explanation="list config",
                 tool="run_command", args={}, privileged=False)


async def test_y_approves():
    app, card, decisions = mount_card(PRIV)
    async with app.run_test() as pilot:
        app.mount(card)
        card.focus()
        await pilot.press("y")
        assert decisions == [True]


async def test_n_declines():
    app, card, decisions = mount_card(PRIV)
    async with app.run_test() as pilot:
        app.mount(card)
        card.focus()
        await pilot.press("n")
        assert decisions == [False]


async def test_privileged_badge_renders():
    app, card, _ = mount_card(PRIV)
    async with app.run_test() as pilot:
        app.mount(card)
        assert "PRIVILEGED" in str(card.content)
        assert "o-pkg install htop" in str(card.content)


async def test_unprivileged_no_badge():
    app, card, _ = mount_card(PLAIN)
    async with app.run_test() as pilot:
        app.mount(card)
        assert "PRIVILEGED" not in str(card.content)
        assert "ls /etc" in str(card.content)


async def test_chat_app_mounts_card_on_proposal(tmp_path):
    agent = FakeAgent(script=[ProposalShown(PRIV)])
    app = ChatApp(agent, History(tmp_path / "h.jsonl"))
    async with app.run_test() as pilot:
        app.query_one("#input").value = "install htop"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        cards = app.query(ConfirmCard)
        assert len(cards) == 1
        assert "PRIVILEGED" in str(cards[0].content)


async def test_slash_input_routed_to_tui_not_agent(tmp_path):
    agent = FakeAgent(script=[])
    app = ChatApp(agent, History(tmp_path / "h.jsonl"))
    async with app.run_test() as pilot:
        app.query_one("#input").value = "/about"
        await pilot.press("enter")
        await pilot.pause()
        assert agent.calls == []  # never reached the model
