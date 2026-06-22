"""Meta commands: /help, and a friendly auto-intro when Lorehound is @mentioned."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import ui


def capabilities_card(bot: commands.Bot) -> discord.ui.LayoutView:
    """A single source of truth for 'what can Lorehound do', reused by /help and
    the @mention reply. A Components V2 card (see lorehound/ui.py)."""
    icon = bot.user.display_avatar.url if bot.user is not None else None
    return ui.card(
        ui.header("# 🐕 Lorehound — your tabletop RPG helper", icon_url=icon),
        ui.text(
            "I roll dice and search a **Rules Library** — the game books loaded "
            "into my index — so you can pull up rules, gear, and stats mid-session "
            "without digging through PDFs. I work across multiple systems; run "
            "`/sources` to see what's available. Type `/` to browse every command."
        ),
        ui.separator(),
        ui.text(
            "**🎲 Dice**\n"
            "`/roll dice:2d6+1` — roll any dice expression\n"
            "`/d sides:20 count:2` — quick-roll N dice of one type\n"
            "`/t2k attribute:B skill:C ammo:5` — Twilight 2000 check "
            "(add `ammo` dice for full-auto)"
        ),
        ui.separator(),
        ui.text(
            "**📚 Rules Library** — search the loaded books, then pick a result to read:\n"
            "`/rule source:<game> query:<topic>` — how to play: stats, abilities, specialties\n"
            "`/item source:<game> query:<topic>` — gear, weapons, equipment\n"
            "`/transport source:<game> query:<topic>` — vehicles, ships, craft & parts\n"
            "`/table source:<game> name:<table>` — print a rules table\n"
            "`/sources` — list games & books  ·  `/reindex` — re-index the library (operator)"
        ),
        ui.separator(),
        ui.text("-# `/help` shows this · you can also **@mention me** anytime"),
        accent=discord.Colour.blurple(),
    )


class MetaCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="Show what Lorehound can do.")
    async def help_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            view=capabilities_card(self.bot), ephemeral=True
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
                    view=capabilities_card(self.bot), mention_author=False
                )
            except discord.HTTPException:
                pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MetaCog(bot))
