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
from typing import NamedTuple

import discord
from discord import app_commands
from discord.ext import commands

from .. import ui
from ..rules import ReindexInProgress, RulesService, table_topic
from ..search_index import Chunk, SearchHit, name_match_score
from ..tables import _name_col, render_item, render_stat_box, render_table
from ..text_utils import clean_grid

TEAL = discord.Colour.dark_teal()
GREEN = discord.Colour.green()


class _Category(NamedTuple):
    """Presentation metadata for one result category: its badge emoji, display
    label, accent colour, and whether the unified /lookup includes it."""
    emoji: str
    label: str
    accent: discord.Colour
    in_lookup: bool


# Single source of truth for per-category presentation. Each result type reads
# distinctly (like the dice cards): its own badge emoji, label, and accent colour.
# ``in_lookup=False`` excludes a category from the unified /lookup — "reference"
# (the book's alphabetical index / page-footer fragments) is clutter, so it's out.
CATEGORIES: dict[str, _Category] = {
    "rules": _Category("📖", "Rules", discord.Colour.blurple(), True),
    "items": _Category("🎒", "Items", discord.Colour.gold(), True),
    "transport": _Category("🚙", "Transport", discord.Colour.blue(), True),
    "tables": _Category("📊", "Tables", discord.Colour.green(), True),
    "card": _Category("🪖", "Careers", discord.Colour.dark_red(), True),
    "spell": _Category("✨", "Spell", discord.Colour.purple(), True),
    "feat": _Category("🎯", "Feat", discord.Colour.dark_orange(), True),
    # Excluded from /lookup; emoji/label/accent match the old unknown-category
    # fallbacks (📖 / "" / TEAL) so any stray reference hit renders identically.
    "reference": _Category("📖", "", TEAL, False),
}
# Categories the unified /lookup skips (derived from the registry so the call site
# keeps its exact ``category not in _LOOKUP_SKIP`` membership test). == {"reference"}.
_LOOKUP_SKIP = {name for name, c in CATEGORIES.items() if not c.in_lookup}


def _accent(category: str) -> discord.Colour:
    c = CATEGORIES.get(category)
    return c.accent if c is not None else TEAL


def _meta(category: str) -> tuple[str, str]:
    """``(emoji, label)`` for a category, with the unknown-category fallback the
    old ``_META.get(...)`` sites used (a generic 📖 book and an empty label)."""
    c = CATEGORIES.get(category)
    return (c.emoji, c.label) if c is not None else ("📖", "")


_NOT_CONFIGURED = (
    "📚 Google Drive isn't connected yet. Add your `DRIVE_FOLDER_ID` and Google "
    "service-account credentials (see the README), then run `/reindex`."
)
_NOT_READY = (
    "📚 Nothing indexed yet — give it a minute after startup, or run `/reindex`."
)


def _badge(category: str) -> str:
    """The type emoji for a result category (used by /lookup's mixed list)."""
    return _meta(category)[0]


def _explode_to_items(hits: list[SearchHit], query: str) -> list[SearchHit]:
    """Expand catalog tables (weapon/vehicle lists) into one pickable entry per item
    row — so a wide table becomes a name pick-list, each resolving to a Stat|Value
    card. Each entry is scored by name overlap with the query. Non-catalog hits (prose,
    rules tables, single-item stat blocks) pass through but are normalized into [0, 0.5]
    so a mere mention can never outrank a real item card (a BM25 prose hit can score
    20+, which used to bury the fractional row scores). Highest-first."""
    exploded: list[SearchHit] = []
    passthrough: list[SearchHit] = []
    seen: set[tuple] = set()
    for h in hits:
        rows = clean_grid(h.chunk.rows or [])
        name_col = _name_col(rows) if rows else 0
        # Distinct item-like names in the name column: a real catalog has ≥2; a
        # single-item stat block (T2K weapon card — name in the title, not a column)
        # yields ≤1, so it passes through and renders whole instead of being shredded.
        catalog_names = {
            v.lower() for r in rows[1:]
            if name_col < len(r) and (v := r[name_col].strip())
            and any(ch.isalpha() for ch in v)
            and not (v.replace(" ", "").isalpha() and v.isupper())
        }
        if len(rows) < 3 or len(rows[0]) < 4 or len(catalog_names) < 2:
            passthrough.append(h)
            continue
        header = rows[0]
        table = h.chunk.section.split("›")[-1].strip()
        for row in rows[1:]:
            # Normalize away trailing footnote markers ("BMP-1*" == "BMP-1") so the
            # same vehicle from a full table + a fragment re-detection collapses.
            name = (row[name_col].strip().rstrip(" *†‡").strip()) if name_col < len(row) else ""
            if not name:
                continue
            key = (h.chunk.source, name.lower())
            if key in seen:
                continue
            seen.add(key)
            score = name_match_score(query, name)
            exploded.append(SearchHit(
                chunk=Chunk(
                    game=h.chunk.game, source=h.chunk.source, category=h.chunk.category,
                    section=f"{table} › {name}" if table else name,
                    locator=h.chunk.locator, text=name, rows=[header, row],
                ),
                score=score,
            ))
    if passthrough:
        top = max((h.score for h in passthrough), default=0.0) or 1.0
        passthrough = [SearchHit(chunk=h.chunk, score=0.5 * h.score / top) for h in passthrough]
    out = exploded + passthrough
    out.sort(key=lambda x: x.score, reverse=True)
    return out


def _merge_item_hits(direct: list[SearchHit], exploded: list[SearchHit]) -> list[SearchHit]:
    """Merge directly-resolved catalog cards with explode/passthrough hits, dropping
    duplicates of the same item (same book + name), keeping the higher score."""
    out: list[SearchHit] = []
    seen: set[tuple] = set()
    for h in sorted([*direct, *exploded], key=lambda x: x.score, reverse=True):
        key = (h.chunk.source, h.chunk.section.split("›")[-1].strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


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


async def _catalog_autocomplete(
    interaction: discord.Interaction, current: str, category: str
) -> list[app_commands.Choice[str]]:
    """Suggest item names from a game's weapon/vehicle catalogs (deduped at index
    time). Free-text still works — these are just suggestions to browse by."""
    rules: RulesService = interaction.client.rules_service  # type: ignore[attr-defined]
    game = _resolve_game(rules, getattr(interaction.namespace, "source", "") or "")
    if game is None:
        return []
    cur = current.lower()
    return [
        app_commands.Choice(name=n[:100], value=n[:100])
        for n in rules.catalog_names(game, category)
        if cur in n.lower()
    ][:25]


async def _item_name_autocomplete(interaction, current):  # noqa: ANN001
    return await _catalog_autocomplete(interaction, current, "items")


async def _transport_name_autocomplete(interaction, current):  # noqa: ANN001
    return await _catalog_autocomplete(interaction, current, "transport")


async def _spell_name_autocomplete(interaction, current):  # noqa: ANN001
    return await _catalog_autocomplete(interaction, current, "spell")


async def _feat_name_autocomplete(interaction, current):  # noqa: ANN001
    return await _catalog_autocomplete(interaction, current, "feat")


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


async def _career_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Career names for the chosen game. Empty for systems without structured
    careers (e.g. Traveller) — there the user free-types and /class assembles."""
    rules: RulesService = interaction.client.rules_service  # type: ignore[attr-defined]
    game = _resolve_game(rules, getattr(interaction.namespace, "source", "") or "")
    if game is None:
        return []
    cur = current.lower()
    return [
        app_commands.Choice(name=n, value=n)
        for n in rules.career_names(game)
        if cur in n.lower()
    ][:25]


async def _table_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    """Tables for the chosen game, grouped by topic (Combat / Health / Character /
    Travel / …) so the list reads as ``Topic › Table`` instead of a flat dump."""
    rules: RulesService = interaction.client.rules_service  # type: ignore[attr-defined]
    game = _resolve_game(rules, getattr(interaction.namespace, "source", "") or "")
    if game is None:
        return []
    cur = current.lower()
    seen: set[tuple] = set()
    items: list[tuple[str, str, str]] = []  # (topic, leaf, display)
    for c in rules.index.chunks:
        if c.category != "tables" or c.game != game or not c.section:
            continue
        chapter = c.section.split("›")[0].strip()
        leaf = c.section.split("›")[-1].strip()
        if not leaf:
            continue
        topic = table_topic(chapter, leaf)
        disp = f"{topic} › {leaf}" + (f" · {c.locator}" if c.locator else "")
        if cur and cur not in disp.lower():
            continue
        key = (topic.lower(), leaf.lower(), c.locator)
        if key in seen:
            continue
        seen.add(key)
        items.append((topic, leaf, disp))
    items.sort(key=lambda x: (x[0], x[1]))  # cluster by topic, then table name
    return [
        app_commands.Choice(name=disp[:100], value=leaf[:100])
        for _topic, leaf, disp in items[:25]
    ]


# --- Select-to-read card ----------------------------------------------------


def _where(chunk) -> str:
    return chunk.source + (f" · {chunk.locator}" if chunk.locator else "")


def _detail_items(hit: SearchHit, query: str) -> list[discord.ui.Item]:
    """The body blocks for one result — a title, the text or rendered table, and
    a source/page footnote. Reused by the inline detail and the public repost."""
    c = hit.chunk
    emoji, _ = _meta(c.category)
    heading = (c.section or query)[:250]
    if c.rows:  # any table chunk (rules table, weapon/vehicle stat block)
        # For gear lookups, pull just the matching item's row as a Stat|Value card
        # titled by the item name; for rules tables show the whole table.
        if c.category in ("spell", "feat"):
            # A stat-box card: level + fields as a Stat|Value block, then the prose.
            rendered, wide = render_stat_box(c.rows, c.description)
            if "›" in c.section:
                heading = c.section.split("›")[-1].strip()[:250]
        elif c.category in ("items", "transport"):
            rendered, wide, item_name = render_item(c.rows, query)
            if item_name:  # matched a single item → use its name as the header
                heading = item_name[:250]
            elif "›" in c.section:  # stat-block card (ship / wrapped weapon): name is the leaf
                heading = c.section.split("›")[-1].strip()[:250]
        else:
            rendered, wide = render_table(c.rows)
        note = (
            " — wide table; scroll sideways on mobile. Verify against the book."
            if wide
            else " — verify against the book for rulings."
        )
        return [ui.text(f"### {emoji} {heading}"), ui.separator(), ui.text(rendered[:4000]),
                ui.text(f"-# {_where(c)}{note}")]
    title = ui.text(f"### {emoji} {heading}")
    body = " ".join(c.text.split())[:4000]
    return [title, ui.separator(), ui.text(body),
            ui.text(f"-# {_where(c)} — verify against the book for rulings.")]


def _public_detail_card(hit: SearchHit, query: str, user) -> discord.ui.LayoutView:
    """A non-interactive card reposting one result to the whole channel."""
    return ui.card(
        ui.header(f"-# 📢 Shared by **{user.display_name}**", icon_url=user.display_avatar.url),
        *_detail_items(hit, query),
        accent=_accent(hit.chunk.category),
    )


async def _share_card(interaction: discord.Interaction, public_card) -> None:
    """Share a result to the channel: post a clean standalone message (not a reply
    tied to the interaction) and remove the private ephemeral lookup card."""
    channel = interaction.channel
    if channel is None:  # DM / no sendable channel — just post as the response
        await interaction.response.send_message(view=public_card)
        return
    await interaction.response.defer()  # ack the button press
    try:
        await channel.send(view=public_card)  # standalone channel message (bot is a member)
    except discord.Forbidden:
        # User-installed app with no access to this channel (50001 Missing Access) — post
        # via the interaction webhook instead, which is publicly visible and doesn't
        # require the bot to be a guild member.
        try:
            await interaction.followup.send(view=public_card)
        except discord.HTTPException as exc:
            await interaction.followup.send(f"⚠️ Couldn't post that here: {exc}", ephemeral=True)
            return
    except discord.HTTPException as exc:
        await interaction.followup.send(f"⚠️ Couldn't post that here: {exc}", ephemeral=True)
        return
    try:
        await interaction.delete_original_response()  # remove the ephemeral card
    except discord.HTTPException:
        pass  # best-effort; the share already succeeded


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
        await _share_card(
            interaction, _public_detail_card(self.selected_hit, self.query, interaction.user)
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
        # Accent by the focused result's type (mixed /lookup lists keep TEAL).
        focus = hits[selected] if selected is not None else (hits[0] if hits else None)
        accent = TEAL if badges else (_accent(focus.chunk.category) if focus else TEAL)
        container = discord.ui.Container(accent_colour=accent)
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


# --- Career (/class) card ---------------------------------------------------

_CAREER_EMOJI = "🪖"


def _career_items(career) -> list[discord.ui.Item]:
    """Body blocks for a career card — title, fields, roll grids, footnote. Shared
    by the ephemeral view and the public repost. Prose fields collapse into one
    block; each grid (e.g. a D6 specialties table) renders as its own table."""
    items: list[discord.ui.Item] = [
        ui.text(f"### {_CAREER_EMOJI} {career.name[:240]}"),
        ui.text(f"in **{career.game}**"),
        ui.separator(),
    ]
    prose, blocks, grids = [], [], []
    for s in career.sections:
        if s.rows:
            block, _wide = render_table(s.rows)
            grids.append((f"**{s.label}**\n{block}" if s.label else block)[:1500])
        elif "✓" in s.text:
            # A checklist field (e.g. starting gear) — render the ✓ items as a list
            # under a bold label, instead of one run-on line.
            picks = [x.strip(" ,") for x in s.text.split("✓") if x.strip(" ,")]
            head = f"**{s.label}:**\n" if s.label else ""
            blocks.append((head + "\n".join(f"• {p}" for p in picks))[:1500])
        elif s.label:
            prose.append(f"**{s.label}:** {s.text}")
        elif s.text:
            prose.append(s.text)
    if prose:
        items.append(ui.text("\n".join(prose)[:3500]))
    for b in blocks:
        items.append(ui.text(b))
    for g in grids[:4]:
        items.append(ui.text(g))
    note = (
        "⚙️ assembled from indexed mentions — verify in the book"
        if career.assembled
        else "verify against the book for rulings"
    )
    loc = f" · {career.locator}" if career.locator else ""
    items.append(ui.separator())
    items.append(ui.text(f"-# {career.source}{loc} · {note}"))
    return items


def _public_career_card(career, user) -> discord.ui.LayoutView:
    return ui.card(
        ui.header(
            f"-# {_CAREER_EMOJI} Shared by **{user.display_name}**",
            icon_url=user.display_avatar.url,
        ),
        *_career_items(career),
        accent=_accent("card"),
    )


class CareerShareButton(discord.ui.Button):
    """Repost a career card publicly (lookups are private by default)."""

    def __init__(self, career) -> None:
        super().__init__(label="Show in channel", emoji="📢", style=discord.ButtonStyle.primary)
        self.career = career

    async def callback(self, interaction: discord.Interaction) -> None:
        await _share_card(interaction, _public_career_card(self.career, interaction.user))


class CareerView(discord.ui.LayoutView):
    """The ephemeral career card + a button to share it to the channel."""

    def __init__(self, career) -> None:
        super().__init__(timeout=180)
        self.career = career
        container = discord.ui.Container(accent_colour=_accent("card"))
        for item in _career_items(career):
            container.add_item(item)
        row = discord.ui.ActionRow()
        row.add_item(CareerShareButton(career))
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

    async def _resolve_or_reject(
        self, interaction: discord.Interaction, source: str
    ) -> str | None:
        """Shared command guard-preamble: Drive configured? index ready? is
        ``source`` a known game? Sends the matching ephemeral error and returns
        ``None`` on any failure, else returns the resolved game name."""
        if self.rules.drive is None:
            await interaction.response.send_message(_NOT_CONFIGURED, ephemeral=True)
            return None
        if not self.rules.ready:
            await interaction.response.send_message(_NOT_READY, ephemeral=True)
            return None
        game = _resolve_game(self.rules, source)
        if game is None:
            available = ", ".join(f"`{g}`" for g in self.rules.index.games) or "(none)"
            await interaction.response.send_message(
                f"⚠️ I don't have a game called **{source}**. Available: {available}",
                ephemeral=True,
            )
            return None
        return game

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
        game = await self._resolve_or_reject(interaction, source)
        if game is None:
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

        selected = None
        if category is None:
            # Unified search: over-fetch, drop the reference index, badge by type.
            hits = self.rules.search(query, game=game, book=chosen_book, top_k=12)
            hits = [h for h in hits if h.chunk.category not in _LOOKUP_SKIP][:8]
            emoji, label, badges = "🔎", "Lookup", True
        else:
            hits = self.rules.search(
                query, game=game, book=chosen_book, category=category, top_k=8
            )
            emoji, label = _meta(category)
            badges = False
            if category in ("items", "transport"):
                # A weapon/vehicle catalog is a list of items, not one wide grid.
                # Resolve the item name straight to its card (BM25 can't surface one
                # row of a long catalog), then merge in exploded/prose hits as the
                # free-text fallback. A single clearly-named match opens directly.
                direct = self.rules.catalog_card_lookup(
                    game, category, query, book=chosen_book
                )
                hits = _merge_item_hits(direct, _explode_to_items(hits, query))[:25]
                if hits and hits[0].score >= 0.6 and sum(1 for h in hits if h.score >= 0.6) == 1:
                    selected = 0
            elif category in ("spell", "feat"):
                # Each spell/feat is its own card — resolve the name directly. Fall
                # back to the BM25 hits (description match) only when nothing names it.
                direct = self.rules.catalog_card_lookup(
                    game, category, query, book=chosen_book
                )
                if direct:
                    hits = direct[:25]
                    if hits[0].score >= 0.6 and sum(1 for h in hits if h.score >= 0.6) == 1:
                        selected = 0
        scope = f"**{game}**" + (f" › **{chosen_book}**" if chosen_book else "")
        if not hits:
            await interaction.response.send_message(
                f"No {label.lower()} matches for **{query}** in {scope}.",
                ephemeral=True,
            )
            return

        noun = "item" if category in ("items", "transport") else "match"
        subtitle = (
            f"in {scope} — **{len(hits)}** {noun}{'es' if noun == 'match' else 's'}. "
            "Pick one to read:"
        )
        view = ResultsView(
            hits,
            query,
            title=f"{emoji} {label}: {query}",
            subtitle=subtitle,
            selected=selected,
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
        query="An item — pick from the list, or type to search",
        book="Optional: narrow to a single book",
    )
    @app_commands.autocomplete(
        source=_game_autocomplete, query=_item_name_autocomplete, book=_book_autocomplete
    )
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
        query="A vehicle/craft — pick from the list, or type to search",
        book="Optional: narrow to a single book",
    )
    @app_commands.autocomplete(
        source=_game_autocomplete, query=_transport_name_autocomplete, book=_book_autocomplete
    )
    async def transport(
        self,
        interaction: discord.Interaction,
        source: str,
        query: str,
        book: str | None = None,
    ) -> None:
        await self._lookup(interaction, "transport", source, query, book)

    @app_commands.command(
        name="spell", description="Show a SPELL/cantrip/focus/ritual card."
    )
    @app_commands.describe(
        source="Which game to search",
        query="A spell — pick from the list, or type to search",
        book="Optional: narrow to a single book",
    )
    @app_commands.autocomplete(
        source=_game_autocomplete, query=_spell_name_autocomplete, book=_book_autocomplete
    )
    async def spell(
        self,
        interaction: discord.Interaction,
        source: str,
        query: str,
        book: str | None = None,
    ) -> None:
        await self._lookup(interaction, "spell", source, query, book)

    @app_commands.command(
        name="feat", description="Show a FEAT card — prerequisites & effect."
    )
    @app_commands.describe(
        source="Which game to search",
        query="A feat — pick from the list, or type to search",
        book="Optional: narrow to a single book",
    )
    @app_commands.autocomplete(
        source=_game_autocomplete, query=_feat_name_autocomplete, book=_book_autocomplete
    )
    async def feat(
        self,
        interaction: discord.Interaction,
        source: str,
        query: str,
        book: str | None = None,
    ) -> None:
        await self._lookup(interaction, "feat", source, query, book)

    @app_commands.command(
        name="class",
        description="Show a CAREER/CLASS card — requirements, skills, specialties & roll tables.",
    )
    @app_commands.describe(
        source="Which game to search",
        career="Which career/class — pick from the list, or type a name",
    )
    @app_commands.autocomplete(source=_game_autocomplete, career=_career_autocomplete)
    async def class_card(
        self, interaction: discord.Interaction, source: str, career: str
    ) -> None:
        game = await self._resolve_or_reject(interaction, source)
        if game is None:
            return
        # find_career may search-assemble (in-memory, fast) for systems without
        # structured cards, so no off-thread work is needed.
        found = self.rules.find_career(game, career)
        if found is None:
            names = self.rules.career_names(game)
            hint = (
                " Try: " + ", ".join(f"`{n}`" for n in names[:8])
                if names
                else " No structured careers for this game yet — try `/lookup` instead."
            )
            await interaction.response.send_message(
                f"No career matching **{career}** in **{game}**.{hint}",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(view=CareerView(found), ephemeral=True)

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
        game = await self._resolve_or_reject(interaction, source)
        if game is None:
            return
        # Stricter relevance: a dominant exact match opens alone instead of dragging
        # in tables that merely share a word (e.g. other "… Modifiers" tables).
        hits = self.rules.search(name, game=game, category="tables", top_k=8, min_rel=0.6)
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
        # Common case: a reindex (or the startup warm) is already running. Reject up
        # front so we don't kick off a duplicate Drive pull + re-extraction.
        if self.rules.indexing:
            await interaction.response.send_message(
                "⏳ A reindex is already running — give it a moment and try again.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            summary = await asyncio.to_thread(self.rules.refresh, force)
        except ReindexInProgress:
            # Lost a race against another refresh that started between the check above
            # and the thread acquiring the lock.
            await interaction.followup.send(
                "⏳ A reindex is already running — give it a moment and try again.",
                ephemeral=True,
            )
            return
        except Exception as exc:  # noqa: BLE001 - surface the error to the user
            await interaction.followup.send(f"⚠️ Reindex failed: {exc}", ephemeral=True)
            return

        games: dict[str, list[str]] = summary["games"]
        blocks = "\n\n".join(
            f"**🎲 {game}** ({len(files)})\n" + "\n".join(f"- {f}" for f in files)
            for game, files in games.items()
        )
        mode = "full re-extract (cache bypassed)" if force else "incremental (changed files only)"
        careers = summary.get("careers", 0)
        view = ui.card(
            ui.text("### ✅ Library reindexed"),
            ui.text(
                f"Indexed **{summary['documents']}** book(s) across "
                f"**{len(games)}** game(s) — **{summary['chunks']}** searchable chunks, "
                f"**{careers}** careers.\n"
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
