"""Meta commands: /help, and a professional auto-intro when Lorehound is @mentioned."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands


def capabilities_embed(bot: commands.Bot) -> discord.Embed:
    """A single source of truth for 'what can Lorehound do', reused by /help
    and the @mention reply."""
    embed = discord.Embed(
        title="🐕 Lorehound — your tabletop RPG helper",
        description=(
            "I roll dice and search a **Rules Library** — the game books loaded "
            "into my index — so you can pull up rules, gear, and stats mid-session "
            "without digging through PDFs. I work across multiple systems; run "
            "`/sources` to see which games and books are available right now. "
            "Type `/` to browse every command, or use the ones below."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="🎲 Dice",
        value=(
            "`/roll dice:2d6+1` — roll any dice expression\n"
            "`/d sides:20 count:2` — quick-roll N dice of one type\n"
            "`/t2k attribute:B skill:C ammo:5` — Twilight 2000 check "
            "(add `ammo` dice for full-auto)"
        ),
        inline=False,
    )
    embed.add_field(
        name="📚 Rules Library (pick a result to read)",
        value=(
            "Search the loaded game books, then pick a result to read in full:\n"
            "`/rule source:<game> query:<topic>` — how to play: stats, abilities, specialties\n"
            "`/item source:<game> query:<topic>` — gear, weapons, equipment\n"
            "`/vehicle source:<game> query:<topic>` — vehicles, ships & their parts\n"
            "`/sources` — list available games & books  ·  `/rules_sync` — re-index the library"
        ),
        inline=False,
    )
    embed.add_field(
        name="ℹ️ Getting help",
        value="`/help` shows this. You can also just **@mention me** anytime.",
        inline=False,
    )
    if bot.user is not None:
        embed.set_thumbnail(url=bot.user.display_avatar.url)
    embed.set_footer(text="Lorehound · a general RPG helper")
    return embed


class MetaCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="Show what Lorehound can do.")
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=capabilities_embed(self.bot), ephemeral=True
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Reply with capabilities when directly @mentioned. Uses only the
        # `mentions` field (no Message Content privileged intent required).
        if message.author.bot or self.bot.user is None:
            return
        if self.bot.user in message.mentions:
            try:
                await message.reply(
                    embed=capabilities_embed(self.bot), mention_author=False
                )
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MetaCog(bot))
