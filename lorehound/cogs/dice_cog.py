"""Slash commands for rolling dice (generic + Twilight 2000).

Output style: "friendly & visual" — a who-rolled header with the roller's
avatar, real Unicode pip-faces for d6, a separator rule, and a prominent total
with ✨ (a max roll showed) / 💥 (a 1 showed) flair.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..dice import DiceError, STANDARD_DICE, evaluate, roll_dice
from ..twilight import TwilightError, skill_check

# Unicode pip faces for d6 (used in monospace code-block tables, e.g. /t2k).
D6_FACES = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}
# Big, colourful keycap emoji for d6 in rich embeds (/roll, /d, /ammo).
KEYCAPS = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣", 4: "4️⃣", 5: "5️⃣", 6: "6️⃣"}
RULE = "─" * 16


def _die_token(face: int, sides: int) -> str:
    """One die's value: a big keycap emoji for d6, a boxed number otherwise."""
    if sides == 6 and 1 <= face <= 6:
        return KEYCAPS[face]
    return f"`{face}`"


def _cb_die(value: int, sides: int) -> str:
    """Code-block-safe die token: pip-face + number for d6, plain number else."""
    if sides == 6 and 1 <= value <= 6:
        return f"{D6_FACES[value]} {value}"
    return str(value)


def _t2k_table(rows: list[tuple[str, str, str, str, str]]) -> str:
    """Aligned monospace table inside a code block. Columns:
    DIE | LVL | TYPE | HITS | ROLLED.  ROLLED comes last so variable-width dice
    faces never knock the earlier columns out of alignment."""
    header = ("DIE", "LVL", "TYPE", "HITS", "ROLLED")

    def row(die: str, lvl: str, typ: str, hits: str, rolled: str) -> str:
        return f"{die:<10}{lvl:<4}{typ:<7}{hits:<6}{rolled}"

    body = "\n".join(row(*r) for r in (header, *rows))
    return f"```\n{body}\n```"


def _group_line(count: int, sides: int, rolls: list[int]) -> str:
    """A line like `🎲 **2d6**   ⚂ 3   ⚄ 5` (handles subtracted groups)."""
    subtracted = any(r < 0 for r in rolls)
    tokens = "   ".join(_die_token(abs(r), sides) for r in rolls)
    sign = "−" if subtracted else ""
    return f"🎲 **{sign}{count}d{sides}**   {tokens}"


def _author(interaction: discord.Interaction, action: str) -> dict:
    return {
        "name": f"{interaction.user.display_name} {action}",
        "icon_url": interaction.user.display_avatar.url,
    }


def _roll_embed(interaction, expression, groups, modifier, total) -> discord.Embed:
    """Shared 'friendly & visual' embed for /roll and /d."""
    crit = any(abs(r) == g.sides for g in groups for r in g.rolls)
    fumble = any(abs(r) == 1 for g in groups for r in g.rolls)

    lines = [_group_line(g.count, g.sides, g.rolls) for g in groups]
    if modifier:
        lines.append(f"➕ **Modifier**   {modifier:+d}")
    lines.append(RULE)
    flair = ("✨ " if crit else "") + ("💥 " if fumble else "")
    lines.append(f"## {flair}Total: {total}")

    embed = discord.Embed(
        description="\n".join(lines),
        color=discord.Color.green() if crit else discord.Color.blurple(),
    )
    embed.set_author(**_author(interaction, f"rolled {expression}"))
    return embed


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
        embed = _roll_embed(
            interaction, result.expression, result.groups, result.modifier, result.total
        )
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
        embed = _roll_embed(
            interaction, f"{count}d{sides}", [group], 0, group.subtotal
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
        description="Twilight 2000 check: attribute die + optional skill & ammo dice.",
    )
    @app_commands.describe(
        attribute="Attribute rating: A=d12, B=d10, C=d8, D=d6 (or a die like d8)",
        skill="Skill rating (optional; leave blank if untrained)",
        ammo="Ammo dice to roll with the attack (optional; D6, each 6 = extra hit)",
    )
    async def t2k(
        self,
        interaction: discord.Interaction,
        attribute: str,
        skill: str | None = None,
        ammo: int | None = None,
    ) -> None:
        try:
            result = skill_check(attribute, skill, ammo)
        except TwilightError as exc:
            await interaction.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        rows: list[tuple[str, str, str, str, str]] = []
        for d in result.dice:
            hits = f"+{d.successes}" if d.successes else "—"
            rows.append(
                (
                    d.label.capitalize(),
                    d.rating,
                    f"d{d.sides}",
                    hits,
                    _cb_die(d.value, d.sides),
                )
            )
        # Show the skill slot even when untrained (level F = no die rolled).
        if not skill or not str(skill).strip():
            rows.append(("Skill", "F", "—", "—", "untrained"))

        # Optional ammo dice, rolled alongside the attack (each 6 = extra hit).
        if result.ammo is not None:
            faces = " ".join(
                f"{D6_FACES[r]}✨" if r == 6 else D6_FACES[r] for r in result.ammo.rolls
            )
            hits = f"+{result.ammo_hits}" if result.ammo_hits else "—"
            rows.append(("Ammo", "—", f"{len(result.ammo.rolls)}×d6", hits, faces))

        if result.succeeded:
            total = result.total_successes
            plural = "" if total == 1 else "es"
            verdict = f"✅ **Success — {total} success{plural}**"
            if result.ammo_hits:
                verdict += f"  ·  {result.successes} check + {result.ammo_hits} ammo"
            color = discord.Color.green()
        else:
            verdict = "❌ **Failure — 0 successes**"
            color = discord.Color.red()

        desc = f"{_t2k_table(rows)}\n{verdict}"
        footer = None
        if result.ammo is not None:
            desc += (
                f"\n🔫 **{result.rounds_spent}** rounds spent "
                f"_(ammo sum {result.ammo.total} + 1)_"
            )
            # 1s are the jam symbol: on a push each costs 1 reliability, 2+ jams.
            if result.jam_ones:
                line = (
                    f"🔧 **{result.jam_ones}× ⚀** — pushing this roll would cost "
                    f"−{result.jam_ones} weapon reliability"
                )
                if result.jam_ones >= 2:
                    line += " **and jam the weapon** 💥"
                desc += f"\n{line}"
            if not result.succeeded and result.ammo_hits:
                footer = "Attack missed — ammo 6s don't add hits on a miss."
        elif result.can_push_warn:
            footer = (
                "A 1 is showing — careful if you push (it can damage you or the "
                "gear you used)."
            )

        embed = discord.Embed(description=desc, color=color)
        embed.set_author(**_author(interaction, "rolled a Twilight 2000 check"))
        if footer:
            embed.set_footer(text=footer)
        await interaction.response.send_message(embed=embed)

    # Ammo dice are never rolled on their own — they're always part of a ranged
    # attack — so they live on /t2k via the `ammo` option, not a separate command.


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DiceCog(bot))
