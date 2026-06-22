"""Slash commands for searching the library pulled from Google Drive.

Library commands, each scoped to a game (and optionally one book), each showing a
pickable list of matches you select to read in full:
  /rule      — how to play: character stats, abilities, specialties, procedures
  /item      — gear, weapons, equipment
  /transport — vehicles, ships, craft, mounts & their parts
  /table     — find and print a rules table

All responses are private (ephemeral) Components V2 cards; a "Show in channel"
button reposts the picked result publicly. Only dice rolls and @mention are public.
"""

from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from .. import ui
from ..rules import RulesService
from ..search_index import SearchHit
from ..tables import render_item, render_table

TEAL = discord.Colour.dark_teal()
GREEN = discord.Colour.green()

_NOT_CONFIGURED = (
    "📚 Google Drive isn't connected yet. Add your `DRIVE_FOLDER_ID` and Google "
    "service-account credentials (see the README), then run `/reindex`."
)
_NOT_READY = (
    "📚 Nothing indexed yet — give it a minute after startup, or run `/reindex`."
)
_META = {
    "rules": ("📖", "Rules"),
    "items": ("🎒", "Items"),
    "transport": ("🚙", "Transport"),
    "tables": ("📊", "Tables"),
    "card": ("🪖", "Careers"),
}
# Categories /lookup searches — everything players care about. "reference" (the
# book's alphabetical index / page-footer fragments) is clutter, so it's excluded.
_LOOKUP_SKIP = {"reference"}


def _badge(category: str) -> str:
    """The type emoji for a result category (used by /lookup's mixed list)."""
    return _META.get(category, ("📖", ""))[0]


def _resolve_game(rules: RulesService, source: str) -> str | None:
    target = (source or "").strip().lower()
    for game in rules.index.games:
        if game.lower() == target:
            return game
    return None


# --- Autocomplete (module-level so all commands can share them) -------------

async def _game_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    rules: RulesService = interaction.client.rules_service  # type: ignore[attr-defined]
    cur = current.lower()
    return [
        app_commands.Choice(name=g, value=g)
        for g in rules.index.games
        if cur in g.lower()
    ][:25]


async def _book_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    rules: RulesService = interaction.client.rules_service  # type: ignore[attr-defined]
    game = _resolve_game(rules, getattr(interaction.namespace, "source", "") or "")
    if game is None:
        return []
    cur = current.lower()
    return [
        app_commands.Choice(name=b, value=b)
        for b in rules.index.files_by_game.get(game, [])
        if cur in b.lower()
    ][:25]


async def _table_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Once a source is chosen, offer the tables available in that game."""
    rules: RulesService = interaction.client.rules_service  # type: ignore[attr-defined]
    game = _resolve_game(rules, getattr(interaction.namespace, "source", "") or "")
    if game is None:
        return []
    cur = current.lower()
    seen: set[str] = set()
    out: list[app_commands.Choice[str]] = []
    tables = sorted(
        (c for c in rules.index.chunks if c.category == "tables" and c.game == game),
        key=lambda c: c.section,  # groups by chapter (the breadcrumb's first part)
    )
    for c in tables:
        disp = c.section + (f" · {c.locator}" if c.locator else "")
        if cur and cur not in disp.lower():
            continue
        key = disp.lower()
        if key in seen:
            continue
        seen.add(key)
        leaf = c.section.split("›")[-1].strip() if c.section else "table"
        out.append(app_commands.Choice(name=disp[:100], value=leaf[:100]))
        if len(out) >= 25:
            break
    return out


# --- Select-to-read card ----------------------------------------------------


def _where(chunk) -> str:
    return chunk.source + (f" · {chunk.locator}" if chunk.locator else "")


def _detail_items(hit: SearchHit, query: str) -> list[discord.ui.Item]:
    """The body blocks for one result — a title, the text or rendered table, and
    a source/page footnote. Reused by the inline detail and the public repost."""
    c = hit.chunk
    emoji, _ = _META.get(c.category, ("📖", ""))
    title = ui.text(f"### {emoji} {(c.section or query)[:250]}")
    if c.rows:  # any table chunk (rules table, weapon/vehicle stat block)
        # For gear lookups, pull just the matching item's row as a card; for rules
        # tables (and ambiguous gear queries) show the whole table.
        if c.category in ("items", "transport"):
            rendered, wide = render_item(c.rows, query)
        else:
            rendered, wide = render_table(c.rows)
        note = (
            " — wide table; scroll sideways on mobile. Verify against the book."
            if wide
            else " — verify against the book for rulings."
        )
        return [title, ui.separator(), ui.text(rendered[:4000]),
                ui.text(f"-# {_where(c)}{note}")]
    body = " ".join(c.text.split())[:4000]
    return [title, ui.separator(), ui.text(body),
            ui.text(f"-# {_where(c)} — verify against the book for rulings.")]


def _public_detail_card(hit: SearchHit, query: str, user) -> discord.ui.LayoutView:
    """A non-interactive card reposting one result to the whole channel."""
    return ui.card(
        ui.text(f"-# 📖 Shared by {user.display_name}"),
        *_detail_items(hit, query),
        accent=TEAL,
    )


class ResultSelect(discord.ui.Select):
    def __init__(self, hits, query, title, subtitle, selected, badges=False):
        self.hits = hits
        self.query = query
        self.title = title
        self.subtitle = subtitle
        options = []
        for i, h in enumerate(hits):
            c = h.chunk
            label = (c.section or c.source).strip() or f"Result {i + 1}"
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    description=_where(c)[:100],
                    value=str(i),
                    default=(selected == i),
                    emoji=_badge(c.category) if badges else None,
                )
            )
        super().__init__(
            placeholder="Pick a result to read…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        idx = int(self.values[0])
        view = ResultsView(
            self.hits, self.query, title=self.title, subtitle=self.subtitle, selected=idx
        )
        await interaction.response.edit_message(view=view)


class ShowInChannelButton(discord.ui.Button):
    """Repost the currently-selected result publicly. Lookups are private by
    default; this shares the picked one with the whole channel."""

    def __init__(self, selected_hit: SearchHit | None, query: str) -> None:
        super().__init__(
            label="Show in channel",
            emoji="📢",
            style=discord.ButtonStyle.primary,
            disabled=selected_hit is None,
        )
        self.selected_hit = selected_hit
        self.query = query

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.selected_hit is None:
            await interaction.response.send_message(
                "Pick a result from the menu first, then press **Show in channel**.",
                ephemeral=True,
            )
            return
        try:
            await interaction.response.send_message(
                view=_public_detail_card(self.selected_hit, self.query, interaction.user)
            )
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                f"⚠️ Couldn't post that here: {exc}", ephemeral=True
            )


class ResultsView(discord.ui.LayoutView):
    """The ephemeral search card: a ranked list (or the picked detail) plus a
    select to switch results and a button to share the picked one."""

    def __init__(
        self,
        hits: list[SearchHit],
        query: str,
        *,
        title: str,
        subtitle: str,
        selected: int | None = None,
        badges: bool = False,
    ) -> None:
        super().__init__(timeout=180)
        self.hits = hits
        self.query = query
        self.selected = selected
        self.badges = badges
        container = discord.ui.Container(accent_colour=TEAL)
        if selected is None:
            container.add_item(ui.text(f"### {title}"))
            container.add_item(ui.text(subtitle))
            container.add_item(ui.separator())
            lines = []
            for i, h in enumerate(hits, 1):
                c = h.chunk
                # In a mixed (/lookup) list, badge each line with its type emoji.
                prefix = f"{_badge(c.category)} " if badges else ""
                lines.append(f"**{i}.** {prefix}{(c.section or c.source)[:240]}\n-# {_where(c)}")
            container.add_item(ui.text("\n".join(lines)[:4000]))
        else:
            for item in _detail_items(hits[selected], query):
                container.add_item(item)
        container.add_item(ui.separator())
        row = discord.ui.ActionRow()
        if selected is None:
            # List view: a dropdown to pick a result.
            row.add_item(ResultSelect(hits, query, title, subtitle, selected, badges=badges))
        else:
            # Detail view: the dropdown is gone — offer to share the picked result.
            row.add_item(ShowInChannelButton(hits[selected], query))
        container.add_item(row)
        self.add_item(container)


class RulesCog(commands.Cog):
    def __init__(self, bot: commands.Bot, rules: RulesService) -> None:
        self.bot = bot
        self.rules = rules

    def _resolve_book(self, game: str, book: str) -> str | None:
        target = (book or "").strip().lower()
        for b in self.rules.index.files_by_game.get(game, []):
            if b.lower() == target:
                return b
        return None

    async def _lookup(
        self,
        interaction: discord.Interaction,
        category: str | None,
        source: str,
        query: str,
        book: str | None,
    ) -> None:
        """Shared search flow. ``category=None`` is the unified /lookup: it searches
        every category (badged by type), skipping the reference index."""
        if self.rules.drive is None:
            await interaction.response.send_message(_NOT_CONFIGURED, ephemeral=True)
            return
        if not self.rules.ready:
            await interaction.response.send_message(_NOT_READY, ephemeral=True)
            return

        game = _resolve_game(self.rules, source)
        if game is None:
            available = ", ".join(f"`{g}`" for g in self.rules.index.games) or "(none)"
            await interaction.response.send_message(
                f"⚠️ I don't have a game called **{source}**. Available: {available}",
                ephemeral=True,
            )
            return

        chosen_book: str | None = None
        if book:
            chosen_book = self._resolve_book(game, book)
            if chosen_book is None:
                books = ", ".join(
                    f"`{b}`" for b in self.rules.index.files_by_game.get(game, [])
                )
                await interaction.response.send_message(
                    f"⚠️ **{book}** isn't a book in **{game}**. Books: {books}",
                    ephemeral=True,
                )
                return

        if category is None:
            # Unified search: over-fetch, drop the reference index, badge by type.
            hits = self.rules.search(query, game=game, book=chosen_book, top_k=12)
            hits = [h for h in hits if h.chunk.category not in _LOOKUP_SKIP][:8]
            emoji, label, badges = "🔎", "Lookup", True
        else:
            hits = self.rules.search(
                query, game=game, book=chosen_book, category=category, top_k=8
            )
            emoji, label = _META[category]
            badges = False
        scope = f"**{game}**" + (f" › **{chosen_book}**" if chosen_book else "")
        if not hits:
            await interaction.response.send_message(
                f"No {label.lower()} matches for **{query}** in {scope}.",
                ephemeral=True,
            )
            return

        view = ResultsView(
            hits,
            query,
            title=f"{emoji} {label}: {query}",
            subtitle=f"in {scope} — **{len(hits)}** matches. Pick one to read:",
            badges=badges,
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    # --- The lookups --------------------------------------------------------

    @app_commands.command(
        name="lookup",
        description="Search EVERYTHING — rules, items, transport, tables & careers, badged by type.",
    )
    @app_commands.describe(
        source="Which game to search",
        query="What to look up — anything: overwatch, AK-74, hit location, ranger",
        book="Optional: narrow to a single book",
    )
    @app_commands.autocomplete(source=_game_autocomplete, book=_book_autocomplete)
    async def lookup(
        self,
        interaction: discord.Interaction,
        source: str,
        query: str,
        book: str | None = None,
    ) -> None:
        await self._lookup(interaction, None, source, query, book)

    @app_commands.command(
        name="rule",
        description="Look up a RULE (how to play: stats, abilities, specialties).",
    )
    @app_commands.describe(
        source="Which game to search",
        query="What to look up, e.g. sniper specialty, encumbrance, initiative",
        book="Optional: narrow to a single book",
    )
    @app_commands.autocomplete(source=_game_autocomplete, book=_book_autocomplete)
    async def rule(
        self,
        interaction: discord.Interaction,
        source: str,
        query: str,
        book: str | None = None,
    ) -> None:
        await self._lookup(interaction, "rules", source, query, book)

    @app_commands.command(
        name="item", description="Look up an ITEM: gear, weapons, equipment."
    )
    @app_commands.describe(
        source="Which game to search",
        query="What to look up, e.g. assault rifle, body armor, medkit",
        book="Optional: narrow to a single book",
    )
    @app_commands.autocomplete(source=_game_autocomplete, book=_book_autocomplete)
    async def item(
        self,
        interaction: discord.Interaction,
        source: str,
        query: str,
        book: str | None = None,
    ) -> None:
        await self._lookup(interaction, "items", source, query, book)

    @app_commands.command(
        name="transport",
        description="Look up TRANSPORT: vehicles, ships, craft, mounts & their parts.",
    )
    @app_commands.describe(
        source="Which game to search",
        query="What to look up, e.g. jump drive, hull armor, APC",
        book="Optional: narrow to a single book",
    )
    @app_commands.autocomplete(source=_game_autocomplete, book=_book_autocomplete)
    async def transport(
        self,
        interaction: discord.Interaction,
        source: str,
        query: str,
        book: str | None = None,
    ) -> None:
        await self._lookup(interaction, "transport", source, query, book)

    @app_commands.command(
        name="table",
        description="Look up and print a rules TABLE (e.g. hit location, fire modifiers).",
    )
    @app_commands.describe(
        source="Which game to search",
        name="Which table — pick from the list, or type to search",
    )
    @app_commands.autocomplete(source=_game_autocomplete, name=_table_autocomplete)
    async def table(
        self, interaction: discord.Interaction, source: str, name: str
    ) -> None:
        if self.rules.drive is None:
            await interaction.response.send_message(_NOT_CONFIGURED, ephemeral=True)
            return
        if not self.rules.ready:
            await interaction.response.send_message(_NOT_READY, ephemeral=True)
            return
        game = _resolve_game(self.rules, source)
        if game is None:
            available = ", ".join(f"`{g}`" for g in self.rules.index.games) or "(none)"
            await interaction.response.send_message(
                f"⚠️ I don't have a game called **{source}**. Available: {available}",
                ephemeral=True,
            )
            return
        hits = self.rules.search(name, game=game, category="tables", top_k=8)
        if not hits:
            await interaction.response.send_message(
                f"No tables matching **{name}** in **{game}**. "
                "Browse with the `name` autocomplete, or try `/sources`.",
                ephemeral=True,
            )
            return
        # One match → open straight on it (no dropdown). Several → show the list
        # so the player picks; the dropdown then disappears on the detail view.
        if len(hits) == 1:
            view = ResultsView(
                hits, name, title=f"📊 Tables: {name}",
                subtitle=f"in **{game}** — 1 match.", selected=0,
            )
        else:
            view = ResultsView(
                hits, name, title=f"📊 Tables: {name}",
                subtitle=f"in **{game}** — **{len(hits)}** matches. Pick one to read:",
            )
        await interaction.response.send_message(view=view, ephemeral=True)

    # --- Library management -------------------------------------------------

    @app_commands.command(
        name="reindex",
        description="(Operator) Re-pull and re-index the library from Google Drive.",
    )
    @app_commands.describe(
        force="Ignore the extraction cache and re-extract every file from scratch (slow).",
    )
    # Hidden from non-managers in guild command lists; the operator check below is
    # the real authorization (default_permissions doesn't apply in DMs).
    @app_commands.default_permissions(manage_guild=True)
    async def reindex(
        self, interaction: discord.Interaction, force: bool = False
    ) -> None:
        # Reindexing re-downloads the operator's private Drive and rebuilds the
        # single index every server shares — so it's operator-only, not a per-guild
        # admin action. Widen this (e.g. to guild admins) only if that changes.
        if not await self.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "🔒 Only the bot operator can reindex the shared library.",
                ephemeral=True,
            )
            return
        if self.rules.drive is None:
            await interaction.response.send_message(_NOT_CONFIGURED, ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            summary = await asyncio.to_thread(self.rules.refresh, force)
        except Exception as exc:  # noqa: BLE001 - surface the error to the user
            await interaction.followup.send(f"⚠️ Reindex failed: {exc}", ephemeral=True)
            return

        games: dict[str, list[str]] = summary["games"]
        blocks = "\n\n".join(
            f"**🎲 {game}** ({len(files)})\n" + "\n".join(f"- {f}" for f in files)
            for game, files in games.items()
        )
        mode = "full re-extract (cache bypassed)" if force else "incremental (changed files only)"
        view = ui.card(
            ui.text("### ✅ Library reindexed"),
            ui.text(
                f"Indexed **{summary['documents']}** book(s) across "
                f"**{len(games)}** game(s) — **{summary['chunks']}** searchable chunks.\n"
                f"-# {mode}"
            ),
            ui.separator(),
            ui.text(blocks[:4000]),
            accent=GREEN,
        )
        await interaction.followup.send(view=view, ephemeral=True)

    @app_commands.command(
        name="sources", description="List the games and books available to search."
    )
    async def sources(self, interaction: discord.Interaction) -> None:
        if not self.rules.ready:
            await interaction.response.send_message(_NOT_READY, ephemeral=True)
            return
        files_by_game = self.rules.index.files_by_game
        lines = []
        for game in self.rules.index.games:
            books = files_by_game[game]
            lines.append(f"- **🎲 {game}** ({len(books)} books)")
            lines.extend(f"  - {b}" for b in books)
        view = ui.card(
            ui.text("### 📚 Available sources"),
            ui.text(
                "Look things up with `/rule`, `/item`, `/transport`, or `/table` "
                "— `source:<game>` and optionally `book:`."
            ),
            ui.separator(),
            ui.text("\n".join(lines)[:4000]),
            ui.text(f"-# {self.rules.index.chunk_count} searchable chunks"),
            accent=TEAL,
        )
        await interaction.response.send_message(view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    # RulesService is attached to the bot in bot.py before extensions load.
    await bot.add_cog(RulesCog(bot, bot.rules_service))  # type: ignore[attr-defined]
