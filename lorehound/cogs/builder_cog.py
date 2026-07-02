"""/build — interactive equipment builders (mix-and-match configurators).

Where ``/character`` walks a life path, ``/build`` assembles a piece of gear from
indexed components within a budget. Traveller powered armour / Battle Dress first: pick
a base suit, see its slot budget and stats, get a built-suit card you can share.

Reuses the system-agnostic chargen flow engine (a builder is just a flow whose steps are
all deliberate choices, so it always runs interactively — no quick/faithful mode). Gated
on the rules index exactly like ``/character``, and snapshots the component catalogue at
session start so an in-flight build stays consistent across a re-index.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands

from .. import ui
from ..builders import registry
from ..chargen.engine import FAITHFUL, ChargenSession
from ..chargen.model import Step
from .rules_cog import _share_card  # reuse the channel-share helper

ACCENT = discord.Colour.dark_teal()

_NOT_CONFIGURED = (
    "⚙️ The rules library isn't set up yet, so I can't build equipment. "
    "Ask the operator to configure the games library."
)
_NOT_READY = (
    "📚 The rules library is still loading — builders aren't available yet. "
    "Give it a moment and try again."
)
_INDEXING = (
    "⏳ The library is re-indexing right now. Hold on a moment so your build uses stable "
    "data, then run `/build` again."
)


def _unsupported(game: str) -> str:
    games = registry.supported_games()
    avail = f" Available so far: {', '.join(games)}." if games else ""
    return f"🚧 There's no builder for **{game}** yet.{avail}"


def _no_such_kind(game: str, kind: str, builders) -> str:
    kinds = ", ".join(f"`{b.kind}`" for b in builders)
    return f"🔎 **{game}** has no **{kind}** builder. Available: {kinds}."


def _pick_a_kind(game: str, builders) -> str:
    kinds = ", ".join(f"`{b.kind}` ({b.noun})" for b in builders)
    return f"🛠️ **{game}** has several things to build — add `kind:` to pick one: {kinds}."


class _Btn(discord.ui.Button):
    """A button that calls a stored coroutine handler (avoids a subclass per action)."""

    def __init__(self, handler, label, *, style=discord.ButtonStyle.secondary, emoji=None):
        super().__init__(label=label, style=style, emoji=emoji)
        self._handler = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._handler(interaction)


class _StepSelect(discord.ui.Select):
    """A dropdown for a CHOICE step; resolving calls the view's advance handler. The
    handler is passed explicitly (``self.view`` isn't reliable inside a LayoutView)."""

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


class BuilderView(discord.ui.LayoutView):
    """Ephemeral interactive builder. One instance per state; each transition builds a
    fresh view from the shared session and edits the message in place."""

    def __init__(
        self,
        *,
        bot: commands.Bot,
        system: registry.SystemBuilder,
        game: str,
        author_id: int,
        session: ChargenSession,
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
                "This build belongs to someone else — run `/build` to start your own.",
                ephemeral=True,
            )
            return False
        return True

    # --- construction ------------------------------------------------------

    def _build(self) -> None:
        container = discord.ui.Container(accent_colour=ACCENT)
        if self.session.complete:
            self._build_sheet(container)
        else:
            self._build_step(container)
        self.add_item(container)

    def _build_step(self, container: discord.ui.Container) -> None:
        step = self.session.current
        assert step is not None
        summary = self.system.render_summary(self.session.draft) if self.system.render_summary else ""
        if summary:
            container.add_item(ui.text(summary))
            container.add_item(ui.separator())
        prompt = f"### {step.prompt}"
        if step.detail:
            prompt += f"\n{step.detail}"
        container.add_item(ui.text(prompt))
        container.add_item(ui.separator())
        row = discord.ui.ActionRow()
        row.add_item(_StepSelect(step, self.advance))
        container.add_item(row)
        # A Select fills its row, so Back goes on its own row underneath.
        if self.session.can_back:
            back_row = discord.ui.ActionRow()
            back_row.add_item(_Btn(self._back, "Back", emoji="◀️"))
            container.add_item(back_row)

    def _build_sheet(self, container: discord.ui.Container) -> None:
        container.add_item(ui.text(self.system.render_sheet(self.session.draft)))
        container.add_item(ui.separator())
        row = discord.ui.ActionRow()
        row.add_item(_Btn(self._restart, "Start over", emoji="🔄"))
        row.add_item(_Btn(self._share, "Show in channel",
                          style=discord.ButtonStyle.primary, emoji="📢"))
        container.add_item(row)

    # --- handlers ----------------------------------------------------------

    async def advance(self, interaction: discord.Interaction, value: str | None) -> None:
        self.session.resolve(value)
        await self._swap(interaction, self.session)

    async def _back(self, interaction: discord.Interaction) -> None:
        self.session.back()
        await self._swap(interaction, self.session)

    async def _restart(self, interaction: discord.Interaction) -> None:
        session = _new_session(self.bot, self.system, self.game)
        await self._swap(interaction, session)

    async def _share(self, interaction: discord.Interaction) -> None:
        card = ui.card(
            ui.header(f"# {self.system.emoji} {self.game} build",
                      icon_url=interaction.user.display_avatar.url),
            ui.separator(),
            ui.text(self.system.render_sheet(self.session.draft)),
            accent=ACCENT,
        )
        await _share_card(interaction, card)

    async def _swap(self, interaction: discord.Interaction, session: ChargenSession) -> None:
        nxt = BuilderView(
            bot=self.bot, system=self.system, game=self.game,
            author_id=self.author_id, session=session,
        )
        await interaction.response.edit_message(view=nxt)


def _new_session(bot, system: registry.SystemBuilder, game: str) -> ChargenSession:
    """Start a build: snapshot the component catalogue from the live index NOW, then
    drive the always-interactive flow from that stable snapshot."""
    data = system.build_data(bot.rules_service, game) if system.build_data else None
    make = system.make_draft or (lambda g: _FallbackDraft(game=g))
    return ChargenSession(
        system.build_flow,
        mode=FAITHFUL,
        draft=make(game),
        data=data,
        draft_factory=lambda: make(game),
    )


@dataclass
class _FallbackDraft:
    """Minimal draft for a builder that registered no ``make_draft`` (the engine only
    needs ``game``/``log``/``complete``)."""

    game: str
    log: list = field(default_factory=list)
    complete: bool = False


async def _game_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    rules = getattr(interaction.client, "rules_service", None)
    if rules is None:
        return []
    cur = current.lower()
    games = [g for g in rules.index.games if registry.builder_for(g)]
    return [app_commands.Choice(name=g, value=g) for g in games if cur in g.lower()][:25]


async def _kind_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """The buildable kinds for the game named in the ``source`` option (so a game with
    several buildables — armour, ship, … — offers them here)."""
    rules = getattr(interaction.client, "rules_service", None)
    if rules is None:
        return []
    source = (interaction.namespace.source or "") if interaction.namespace else ""
    game = _resolve_game(rules, source)
    if game is None:
        return []
    cur = current.lower()
    return [
        app_commands.Choice(name=f"{b.kind} — {b.noun}", value=b.kind)
        for b in registry.builders_for(game)
        if cur in b.kind.lower()
    ][:25]


class BuilderCog(commands.Cog):
    def __init__(self, bot: commands.Bot, rules) -> None:
        self.bot = bot
        self.rules = rules

    @app_commands.command(
        name="build",
        description="Build a piece of equipment — e.g. a Traveller powered-armour suit.",
    )
    @app_commands.describe(
        source="Which game to build for",
        kind="What to build (only needed when a game offers several)",
    )
    @app_commands.autocomplete(source=_game_autocomplete, kind=_kind_autocomplete)
    async def build(
        self, interaction: discord.Interaction, source: str, kind: str | None = None
    ) -> None:
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
        builders = registry.builders_for(game)
        if not builders:
            await interaction.response.send_message(_unsupported(game), ephemeral=True)
            return
        if kind:
            system = registry.builder_for(game, kind)
            if system is None:
                await interaction.response.send_message(_no_such_kind(game, kind, builders), ephemeral=True)
                return
        elif len(builders) == 1:
            system = builders[0]
        else:
            await interaction.response.send_message(_pick_a_kind(game, builders), ephemeral=True)
            return
        session = _new_session(self.bot, system, game)
        view = BuilderView(
            bot=self.bot, system=system, game=game,
            author_id=interaction.user.id, session=session,
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
    await bot.add_cog(BuilderCog(bot, bot.rules_service))  # type: ignore[attr-defined]
