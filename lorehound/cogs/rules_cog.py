"""Slash commands for searching the library pulled from Google Drive.

Three lookups, each scoped to a game (and optionally one book), each showing a
pickable list of matches you select to read in full:
  /rule    — how to play: character stats, abilities, specialties, procedures
  /item    — gear, weapons, equipment
  /vehicle — vehicles, ships, and their parts

All responses are private (ephemeral); only dice rolls and @mention are public.
"""

from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from ..rules import RulesService
from ..search_index import SearchHit
from ..tables import render_table

_NOT_CONFIGURED = (
    "📚 Google Drive isn't connected yet. Add your `DRIVE_FOLDER_ID` and Google "
    "service-account credentials (see the README), then run `/rules_sync`."
)
_NOT_READY = (
    "📚 Nothing indexed yet — give it a minute after startup, or run `/rules_sync`."
)
_META = {
    "rules": ("📖", "Rules"),
    "items": ("🎒", "Items"),
    "transport": ("🚙", "Transport"),
    "tables": ("📊", "Tables"),
}


def _resolve_game(rules: RulesService, source: str) -> str | None:
    target = (source or "").strip().lower()
    for game in rules.index.games:
        if game.lower() == target:
            return game
    return None


# --- Autocomplete (module-level so all three commands can share them) -------

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


# --- Select-to-read UI ------------------------------------------------------

def _detail_embed(hit: SearchHit, query: str) -> discord.Embed:
    c = hit.chunk
    emoji, _ = _META.get(c.category, ("📖", ""))
    where = f"{c.source}" + (f" · {c.locator}" if c.locator else "")
    title = f"{emoji} {(c.section or query)[:250]}"

    if c.rows:  # any table chunk (rules table, weapon/vehicle stat block)
        rendered, wide = render_table(c.rows)
        note = (
            " — wide table; scroll sideways on mobile. Verify against the book."
            if wide
            else " — verify against the book for rulings."
        )
        embed = discord.Embed(
            title=title, description=rendered[:4096], color=discord.Color.dark_teal()
        )
        embed.set_footer(text=where + note)
        return embed

    embed = discord.Embed(
        title=title,
        description=" ".join(c.text.split())[:4096],
        color=discord.Color.dark_teal(),
    )
    embed.set_footer(text=where + " — verify against the book for rulings.")
    return embed


class ResultSelect(discord.ui.Select):
    def __init__(self, hits: list[SearchHit], query: str) -> None:
        self.hits = hits
        self.query = query
        options = []
        for i, h in enumerate(hits):
            c = h.chunk
            label = (c.section or c.source).strip() or f"Result {i + 1}"
            desc = c.source + (f" · {c.locator}" if c.locator else "")
            options.append(
                discord.SelectOption(
                    label=label[:100], description=desc[:100], value=str(i)
                )
            )
        super().__init__(
            placeholder="Pick a result to read…",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        hit = self.hits[int(self.values[0])]
        self.view.selected = hit  # remember the choice for the "Show" button
        await interaction.response.edit_message(
            embed=_detail_embed(hit, self.query), view=self.view
        )


class ShowInChannelButton(discord.ui.Button):
    """Re-post the currently-selected result publicly. Lookups are private by
    default; this shares the one you picked with the whole channel."""

    def __init__(self) -> None:
        super().__init__(
            label="Show in channel", emoji="📢", style=discord.ButtonStyle.primary
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: ResultsView = self.view  # type: ignore[assignment]
        if view.selected is None:
            await interaction.response.send_message(
                "Pick a result from the menu first, then press **Show in channel**.",
                ephemeral=True,
            )
            return
        embed = _detail_embed(view.selected, view.query)
        try:
            await interaction.response.send_message(
                content=f"📖 Shared by {interaction.user.mention}",
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as exc:
            await interaction.response.send_message(
                f"⚠️ Couldn't post that here: {exc}", ephemeral=True
            )


class ResultsView(discord.ui.View):
    def __init__(self, hits: list[SearchHit], query: str) -> None:
        super().__init__(timeout=180)
        self.query = query
        self.selected: SearchHit | None = None
        self.add_item(ResultSelect(hits, query))
        self.add_item(ShowInChannelButton())


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
        category: str,
        source: str,
        query: str,
        book: str | None,
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

        hits = self.rules.search(
            query, game=game, book=chosen_book, category=category, top_k=8
        )
        emoji, label = _META[category]
        scope = f"**{game}**" + (f" › **{chosen_book}**" if chosen_book else "")
        if not hits:
            await interaction.response.send_message(
                f"No {label.lower()} matches for **{query}** in {scope}.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"{emoji} {label}: {query}",
            description=f"in {scope} — **{len(hits)}** matches. Pick one to read:",
            color=discord.Color.dark_teal(),
        )
        for i, h in enumerate(hits, 1):
            c = h.chunk
            where = c.source + (f" · {c.locator}" if c.locator else "")
            embed.add_field(
                name=f"{i}. {(c.section or c.source)[:240]}",
                value=where[:1024],
                inline=False,
            )
        await interaction.response.send_message(
            embed=embed, view=ResultsView(hits, query), ephemeral=True
        )

    # --- The three lookups --------------------------------------------------

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
        # Show the best match immediately; the select lets you switch among
        # other matches, and the button shares it to the channel.
        view = ResultsView(hits, name)
        view.selected = hits[0]
        await interaction.response.send_message(
            embed=_detail_embed(hits[0], name), view=view, ephemeral=True
        )

    # --- Library management -------------------------------------------------

    @app_commands.command(
        name="rules_sync", description="Re-pull and re-index the library from Drive."
    )
    async def rules_sync(self, interaction: discord.Interaction) -> None:
        if self.rules.drive is None:
            await interaction.response.send_message(_NOT_CONFIGURED, ephemeral=True)
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            summary = await asyncio.to_thread(self.rules.refresh)
        except Exception as exc:  # noqa: BLE001 - surface the error to the user
            await interaction.followup.send(f"⚠️ Sync failed: {exc}", ephemeral=True)
            return

        games: dict[str, list[str]] = summary["games"]
        embed = discord.Embed(
            title="✅ Library synced",
            description=(
                f"Indexed **{summary['documents']}** book(s) across "
                f"**{len(games)}** game(s) — **{summary['chunks']}** searchable chunks."
            ),
            color=discord.Color.green(),
        )
        for game, files in games.items():
            embed.add_field(
                name=f"🎲 {game} ({len(files)})",
                value="\n".join(f"• {f}" for f in files)[:1024],
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="sources", description="List the games and books available to search."
    )
    async def sources(self, interaction: discord.Interaction) -> None:
        if not self.rules.ready:
            await interaction.response.send_message(_NOT_READY, ephemeral=True)
            return
        files_by_game = self.rules.index.files_by_game
        lines = [
            "Look things up with `/rule`, `/item`, or `/vehicle` "
            "— `source:<game>` and optionally `book:`.\n",
        ]
        for game in self.rules.index.games:
            books = files_by_game[game]
            lines.append(f"- **🎲 {game}** ({len(books)} books)")
            lines.extend(f"  - {b}" for b in books)
        embed = discord.Embed(
            title="📚 Available sources",
            description="\n".join(lines)[:4096],
            color=discord.Color.dark_teal(),
        )
        embed.set_footer(text=f"{self.rules.index.chunk_count} searchable chunks")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    # RulesService is attached to the bot in bot.py before extensions load.
    await bot.add_cog(RulesCog(bot, bot.rules_service))  # type: ignore[attr-defined]
