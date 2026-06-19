"""Slash commands for rolling dice (generic + Twilight 2000)."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..dice import DiceError, STANDARD_DICE, evaluate, roll_dice
from ..twilight import TwilightError, ammo_dice, skill_check


class DiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # --- Generic roller -----------------------------------------------------

    @app_commands.command(
        name="roll", description="Roll dice by notation, e.g. 2d6+1 or 3d8-2."
    )
    @app_commands.describe(dice="Dice expression like 2d6+1, d20, or 2d6 + 1d8 + 3")
    async def roll(self, interaction: discord.Interaction, dice: str) -> None:
        try:
            result = evaluate(dice)
        except DiceError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🎲 {result.expression}",
            description=f"**Total: {result.total}**",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Breakdown", value=result.breakdown()[:1024], inline=False)
        await interaction.response.send_message(embed=embed)

    # --- Quick dice (d6..d12 and friends) -----------------------------------

    @app_commands.command(
        name="d", description="Quick-roll N dice of one type, e.g. /d sides:6 count:3"
    )
    @app_commands.describe(
        sides="Die size (4, 6, 8, 10, 12, 20, 100)", count="How many (default 1)"
    )
    async def quick(
        self, interaction: discord.Interaction, sides: int, count: int = 1
    ) -> None:
        try:
            group = roll_dice(count, sides)
        except DiceError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        rolls_str = ", ".join(str(r) for r in group.rolls)
        title = f"🎲 {count}d{sides}"
        desc = f"**Total: {group.subtotal}**\nRolls: {rolls_str}"
        embed = discord.Embed(
            title=title, description=desc, color=discord.Color.blurple()
        )
        await interaction.response.send_message(embed=embed)

    @quick.autocomplete("sides")
    async def _sides_ac(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        return [
            app_commands.Choice(name=f"d{s}", value=s)
            for s in STANDARD_DICE
            if current in str(s) or not current
        ][:25]

    # --- Twilight 2000 skill check ------------------------------------------

    @app_commands.command(
        name="t2k",
        description="Twilight 2000 check: attribute die + optional skill die.",
    )
    @app_commands.describe(
        attribute="Attribute rating: A=d12, B=d10, C=d8, D=d6 (or a die like d8)",
        skill="Skill rating (optional; leave blank if untrained)",
    )
    async def t2k(
        self,
        interaction: discord.Interaction,
        attribute: str,
        skill: str | None = None,
    ) -> None:
        try:
            result = skill_check(attribute, skill)
        except TwilightError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        lines = []
        for d in result.dice:
            marks = []
            if d.is_success:
                marks.append("✅")
            if d.is_one:
                marks.append("⚠️1")
            suffix = (" " + " ".join(marks)) if marks else ""
            lines.append(f"`{d.label} (d{d.sides})` → **{d.value}**{suffix}")

        if result.succeeded:
            headline = f"✅ Success — {result.successes} success(es)"
            color = discord.Color.green()
        else:
            headline = "❌ Failure — 0 successes"
            color = discord.Color.red()

        embed = discord.Embed(title=headline, color=color)
        embed.add_field(name="Dice", value="\n".join(lines), inline=False)
        if result.can_push_warn:
            embed.set_footer(text="A 1 is showing — careful if you push this roll.")
        await interaction.response.send_message(embed=embed)

    # --- Ammo dice ----------------------------------------------------------

    @app_commands.command(
        name="ammo", description="Roll Twilight 2000 ammo dice (D6). 6s = extra hits."
    )
    @app_commands.describe(count="How many ammo dice to roll")
    async def ammo(self, interaction: discord.Interaction, count: int) -> None:
        try:
            result = ammo_dice(count)
        except TwilightError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        rolls_str = ", ".join(
            f"**{r}**" if r >= 6 else str(r) for r in result.rolls
        )
        embed = discord.Embed(
            title=f"🔫 {count} ammo dice",
            color=discord.Color.dark_gold(),
        )
        embed.add_field(name="Rolls", value=rolls_str, inline=False)
        embed.add_field(name="Extra hits (6s)", value=str(result.extra_hits))
        if result.ones:
            embed.add_field(name="1s rolled", value=str(result.ones))
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DiceCog(bot))
