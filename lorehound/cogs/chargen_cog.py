"""/character — interactive, system-agnostic character generation.

The command resolves the game, snapshots its data, and opens an ephemeral flow:
first a mode picker (guided quick-gen vs. faithful step-by-step), then one card per
step driven by :class:`~lorehound.chargen.engine.ChargenSession`. The character can
be re-rolled or shared to the channel when finished.

Generation is gated on the rules index: it refuses to start while the library is
still loading or re-indexing, and snapshots the system's data at session start so an
in-flight character stays consistent if a re-index lands mid-flow.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import ui
from ..chargen import registry, render
from ..chargen.engine import FAITHFUL, QUICK, ChargenSession
from ..chargen.model import CharacterDraft, Step, StepKind
from .rules_cog import _share_card  # reuse the channel-share helper

ACCENT = discord.Colour.dark_gold()

_NOT_CONFIGURED = (
    "⚙️ The rules library isn't set up yet, so I can't build characters. "
    "Ask the operator to configure the games library."
)
_NOT_READY = (
    "📚 The rules library is still loading — character generation isn't available "
    "yet. Give it a moment and try again."
)
_INDEXING = (
    "⏳ The library is re-indexing right now. Hold on a moment so your character is "
    "built from stable data, then run `/character` again."
)


def _unsupported(game: str) -> str:
    games = registry.supported_games()
    avail = f" Supported so far: {', '.join(games)}." if games else ""
    return f"🚧 Character generation isn't available for **{game}** yet.{avail}"


class _Btn(discord.ui.Button):
    """A button that calls a stored coroutine handler — lets the view wire each
    button to ``ChargenView`` behaviour without a subclass per action."""

    def __init__(self, handler, label, *, style=discord.ButtonStyle.secondary, emoji=None):
        super().__init__(label=label, style=style, emoji=emoji)
        self._handler = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._handler(interaction)


class _StepSelect(discord.ui.Select):
    """A dropdown for a CHOICE step; resolving calls the view's advance handler.
    The handler is passed in explicitly rather than read off ``self.view`` (which
    isn't reliably set for components nested in a LayoutView)."""

    def __init__(self, step: Step, on_pick):
        options = [
            discord.SelectOption(
                label=o.label[:100],
                value=o.value[:100],
                description=(o.description or "")[:100] or None,
            )
            for o in step.options[:25]
        ]
        super().__init__(placeholder="Choose…", min_values=1, max_values=1, options=options)
        self._on_pick = on_pick

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._on_pick(interaction, self.values[0])


class _NameModal(discord.ui.Modal, title="Name your character"):
    """A short text prompt for the character's name, launched from the sheet."""

    name = discord.ui.TextInput(
        label="Character name", required=False, max_length=80,
        placeholder="e.g. Sgt. Maria Ramos",
    )

    def __init__(self, view: ChargenView) -> None:
        super().__init__()
        self._view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        chosen = self.name.value.strip()
        if chosen and self._view.session is not None:
            self._view.session.draft.name = chosen
        await self._view._swap(interaction, self._view.session)


class ChargenView(discord.ui.LayoutView):
    """Ephemeral interactive character sheet. One instance per state; each transition
    builds a fresh view from the shared :class:`ChargenSession` and edits the message
    (the same pattern as the rules ``ResultsView``)."""

    def __init__(
        self,
        *,
        bot: commands.Bot,
        system: registry.SystemChargen,
        game: str,
        author_id: int,
        session: ChargenSession | None,
    ) -> None:
        super().__init__(timeout=600)
        self.bot = bot
        self.system = system
        self.game = game
        self.author_id = author_id
        self.session = session
        self._build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This character sheet belongs to someone else — run `/character` to build your own.",
                ephemeral=True,
            )
            return False
        return True

    # --- construction ------------------------------------------------------

    def _build(self) -> None:
        container = discord.ui.Container(accent_colour=ACCENT)
        if self.session is None:
            self._build_mode_picker(container)
        elif self.session.complete:
            self._build_sheet(container)
        else:
            self._build_step(container)
        self.add_item(container)

    def _build_mode_picker(self, container: discord.ui.Container) -> None:
        container.add_item(ui.header(f"# 🎖️ Build a {self.game} character"))
        container.add_item(ui.text(
            "How do you want to build it?\n"
            "• **Guided quick-gen** — I roll and pick for you, pausing only for the real decisions.\n"
            "• **Step-by-step** — you make every choice and roll, by the book."
        ))
        container.add_item(ui.separator())
        row = discord.ui.ActionRow()
        row.add_item(_Btn(self._make_mode_picker(QUICK), "Guided quick-gen",
                          style=discord.ButtonStyle.primary, emoji="⚡"))
        row.add_item(_Btn(self._make_mode_picker(FAITHFUL), "Step-by-step", emoji="📖"))
        container.add_item(row)

    def _build_step(self, container: discord.ui.Container) -> None:
        assert self.session is not None
        step = self.session.current
        assert step is not None
        summary = render.draft_summary(self.session.draft)
        if summary:
            container.add_item(ui.text(summary))
            container.add_item(ui.separator())
        container.add_item(ui.text(render.step_prompt(step)))
        container.add_item(ui.separator())
        row = discord.ui.ActionRow()
        if step.kind == StepKind.CHOICE:
            row.add_item(_StepSelect(step, self.advance))
        elif step.kind == StepKind.ROLL:
            row.add_item(_Btn(self._make_advance(), "Roll",
                              style=discord.ButtonStyle.primary, emoji="🎲"))
        else:  # INFO
            row.add_item(_Btn(self._make_advance(), "Continue",
                              style=discord.ButtonStyle.primary, emoji="▶️"))
        container.add_item(row)
        # A Select fills its row, so Back goes on its own row underneath.
        if self.session.can_back:
            back_row = discord.ui.ActionRow()
            back_row.add_item(_Btn(self._back, "Back", emoji="◀️"))
            container.add_item(back_row)

    def _build_sheet(self, container: discord.ui.Container) -> None:
        assert self.session is not None
        container.add_item(ui.text(render.character_sheet(self.session.draft)))
        container.add_item(ui.separator())
        row = discord.ui.ActionRow()
        row.add_item(_Btn(self._name, "Name", emoji="✏️"))
        row.add_item(_Btn(self._reroll, "Re-roll", emoji="🔄"))
        row.add_item(_Btn(self._share, "Show in channel",
                          style=discord.ButtonStyle.primary, emoji="📢"))
        container.add_item(row)

    # --- handlers ----------------------------------------------------------

    def _make_mode_picker(self, mode: str):
        async def handler(interaction: discord.Interaction) -> None:
            session = _new_session(self.bot, self.system, self.game, mode)
            await self._swap(interaction, session)
        return handler

    def _make_advance(self):
        async def handler(interaction: discord.Interaction) -> None:
            await self.advance(interaction, None)
        return handler

    async def advance(self, interaction: discord.Interaction, value: str | None) -> None:
        assert self.session is not None
        self.session.resolve(value)
        await self._swap(interaction, self.session)

    async def _back(self, interaction: discord.Interaction) -> None:
        assert self.session is not None
        self.session.back()
        await self._swap(interaction, self.session)

    async def _name(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(_NameModal(self))

    async def _reroll(self, interaction: discord.Interaction) -> None:
        assert self.session is not None
        session = _new_session(self.bot, self.system, self.game, self.session.mode)
        await self._swap(interaction, session)

    async def _share(self, interaction: discord.Interaction) -> None:
        assert self.session is not None
        sheet = ui.card(
            ui.header(f"# 🎖️ {self.game} character",
                      icon_url=interaction.user.display_avatar.url),
            ui.separator(),
            ui.text(render.character_sheet(self.session.draft)),
            accent=ACCENT,
        )
        await _share_card(interaction, sheet)

    async def _swap(self, interaction: discord.Interaction, session: ChargenSession) -> None:
        nxt = ChargenView(
            bot=self.bot, system=self.system, game=self.game,
            author_id=self.author_id, session=session,
        )
        await interaction.response.edit_message(view=nxt)


def _new_session(bot, system: registry.SystemChargen, game: str, mode: str) -> ChargenSession:
    """Start a session: snapshot the system's data from the live index NOW, then
    drive the flow from that stable snapshot."""
    data = system.build_data(bot.rules_service, game) if system.build_data else None
    return ChargenSession(
        system.build_flow,
        mode=mode,
        draft=CharacterDraft(game=game),
        data=data,
    )


async def _game_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    rules = getattr(interaction.client, "rules_service", None)
    if rules is None:
        return []
    cur = current.lower()
    games = [g for g in rules.index.games if registry.chargen_for(g)]
    return [app_commands.Choice(name=g, value=g) for g in games if cur in g.lower()][:25]


class ChargenCog(commands.Cog):
    def __init__(self, bot: commands.Bot, rules) -> None:
        self.bot = bot
        self.rules = rules

    @app_commands.command(
        name="character",
        description="Build a character — guided quick-gen or faithful step-by-step.",
    )
    @app_commands.describe(source="Which game to build a character for")
    @app_commands.autocomplete(source=_game_autocomplete)
    async def character(self, interaction: discord.Interaction, source: str) -> None:
        if self.rules.drive is None:
            await interaction.response.send_message(_NOT_CONFIGURED, ephemeral=True)
            return
        if not self.rules.ready:
            await interaction.response.send_message(_NOT_READY, ephemeral=True)
            return
        if self.rules.indexing:
            await interaction.response.send_message(_INDEXING, ephemeral=True)
            return
        game = _resolve_game(self.rules, source)
        if game is None:
            await interaction.response.send_message(
                f"🔎 I don't have a game called **{source}**. Try `/sources`.", ephemeral=True
            )
            return
        system = registry.chargen_for(game)
        if system is None:
            await interaction.response.send_message(_unsupported(game), ephemeral=True)
            return
        view = ChargenView(
            bot=self.bot, system=system, game=game,
            author_id=interaction.user.id, session=None,
        )
        await interaction.response.send_message(view=view, ephemeral=True)


def _resolve_game(rules, source: str) -> str | None:
    """Match ``source`` to an indexed game name (exact, case-insensitive)."""
    target = (source or "").strip().lower()
    for g in rules.index.games:
        if g.lower() == target:
            return g
    return None


async def setup(bot: commands.Bot) -> None:
    # RulesService is attached to the bot in bot.py before extensions load.
    await bot.add_cog(ChargenCog(bot, bot.rules_service))  # type: ignore[attr-defined]
