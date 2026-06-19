"""Slash commands for searching the rules pulled from Google Drive."""

from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from ..rules import RulesService

_NOT_CONFIGURED = (
    "📚 Google Drive isn't connected yet. Add your `DRIVE_FOLDER_ID` and Google "
    "service-account credentials (see the README), then run `/rules_sync`."
)


class RulesCog(commands.Cog):
    def __init__(self, bot: commands.Bot, rules: RulesService) -> None:
        self.bot = bot
        self.rules = rules

    @app_commands.command(
        name="rule", description="Search the rulebooks for a topic, e.g. recoil."
    )
    @app_commands.describe(query="What to look up (keywords work best for now)")
    async def rule(self, interaction: discord.Interaction, query: str) -> None:
        if self.rules.drive is None:
            await interaction.response.send_message(_NOT_CONFIGURED, ephemeral=True)
            return
        if not self.rules.ready:
            await interaction.response.send_message(
                "📚 No rules indexed yet — run `/rules_sync` first.", ephemeral=True
            )
            return

        hits = self.rules.search(query, top_k=3)
        if not hits:
            await interaction.response.send_message(
                f"No matches for **{query}**.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"📖 {query}", color=discord.Color.dark_teal()
        )
        for hit in hits:
            c = hit.chunk
            where = f"{c.source}" + (f" · {c.locator}" if c.locator else "")
            snippet = c.text.strip().replace("\n", " ")
            if len(snippet) > 600:
                snippet = snippet[:600].rsplit(" ", 1)[0] + "…"
            embed.add_field(name=where, value=snippet, inline=False)
        embed.set_footer(text="Keyword search — verify against the book for rulings.")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="rules_sync", description="Re-pull and re-index the rules from Drive."
    )
    async def rules_sync(self, interaction: discord.Interaction) -> None:
        if self.rules.drive is None:
            await interaction.response.send_message(_NOT_CONFIGURED, ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            # Drive I/O is blocking; keep the event loop responsive.
            summary = await asyncio.to_thread(self.rules.refresh)
        except Exception as exc:  # noqa: BLE001 - surface the error to the user
            await interaction.followup.send(f"⚠️ Sync failed: {exc}")
            return

        sources = "\n".join(f"• {s}" for s in summary["sources"]) or "(none)"
        embed = discord.Embed(
            title="✅ Rules synced",
            description=(
                f"Indexed **{summary['documents']}** document(s), "
                f"**{summary['chunks']}** chunk(s)."
            ),
            color=discord.Color.green(),
        )
        embed.add_field(name="Sources", value=sources[:1024], inline=False)
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="sources", description="List the documents currently indexed."
    )
    async def sources(self, interaction: discord.Interaction) -> None:
        if not self.rules.ready:
            await interaction.response.send_message(
                "No documents indexed yet.", ephemeral=True
            )
            return
        listing = "\n".join(f"• {s}" for s in self.rules.index.sources)
        embed = discord.Embed(
            title="📚 Indexed sources",
            description=listing[:4000],
            color=discord.Color.dark_teal(),
        )
        embed.set_footer(text=f"{self.rules.index.chunk_count} chunks total")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    # RulesService is attached to the bot in bot.py before extensions load.
    await bot.add_cog(RulesCog(bot, bot.rules_service))  # type: ignore[attr-defined]
