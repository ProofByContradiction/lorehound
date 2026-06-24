"""Slash commands for rolling dice (generic + Twilight 2000).

Output style: Components V2 *cards* (see ``lorehound/ui.py``) with the roller's
avatar, an ANSI-colored breakdown of the dice (green = max roll / success,
red = a 1), and a prominent total or verdict. Cards are public; only the rules
lookups are private.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ..dice import STANDARD_DICE, DiceError, evaluate, roll_dice
from ..twilight import TwilightError, skill_check
from .. import ui
from ..ui import Ansi, ansi_block, paint

BLURPLE = discord.Colour.blurple()
GREEN = discord.Colour.green()
RED = discord.Colour.red()


def _face(value: int, sides: int) -> str:
    """A die value for an ANSI block: bold-green on a max roll, bold-red on a 1."""
    v = abs(value)
    cell = str(v)
    if v == sides:
        return paint(cell, Ansi.BOLD, Ansi.GREEN)
    if v == 1:
        return paint(cell, Ansi.BOLD, Ansi.RED)
    return cell


def _heading(interaction: discord.Interaction, action: str) -> discord.ui.Item:
    return ui.header(
        f"### 🎲 **{interaction.user.display_name}** {action}",
        icon_url=interaction.user.display_avatar.url,
    )


# --- Generic roller card ----------------------------------------------------


def _roll_card(interaction, expression, groups, modifier, total) -> discord.ui.LayoutView:
    crit = any(abs(r) == g.sides for g in groups for r in g.rolls)
    fumble = any(abs(r) == 1 for g in groups for r in g.rolls)

    lines = [paint(f"{'DICE':<7} ROLLS", Ansi.BOLD, Ansi.CYAN)]
    for g in groups:
        sign = "−" if any(r < 0 for r in g.rolls) else ""
        label = f"{sign}{g.count}d{g.sides}"
        faces = "  ".join(_face(r, g.sides) for r in g.rolls)
        sub = f"  = {g.subtotal}" if g.count > 1 else ""
        lines.append(f"{label:<7} {faces}{sub}")
    if modifier:
        lines.append(f"{'mod':<7} {modifier:+d}")

    flair = ("✨ " if crit else "") + ("💥 " if fumble else "")
    accent = GREEN if crit else (RED if fumble else BLURPLE)
    return ui.card(
        _heading(interaction, f"rolled `{expression}`"),
        ui.separator(),
        ui.text(ansi_block("\n".join(lines))),
        ui.separator(large=True),
        ui.text(f"## {flair}Total: {total}"),
        accent=accent,
    )


# --- Twilight 2000 card -----------------------------------------------------


def _t2k_card(interaction, result, *, untrained: bool) -> discord.ui.LayoutView:
    lines = [paint(f"{'DICE':<11}{'LVL':<5}{'TYPE':<7}{'ROLL':>4}  HITS", Ansi.BOLD, Ansi.CYAN)]
    for d in result.dice:
        roll = f"{d.value:>4}"
        if d.value == 1:
            roll = paint(roll, Ansi.BOLD, Ansi.RED)
        elif d.successes:
            roll = paint(roll, Ansi.BOLD, Ansi.GREEN)
        hits = f"+{d.successes}" if d.successes else "—"
        lines.append(
            f"{d.label.capitalize():<11}{d.rating:<5}{('d' + str(d.sides)):<7}{roll}  {hits}"
        )
    if untrained:
        lines.append(f"{'Skill':<11}{'F':<5}{'—':<7}{'—':>4}  —")
    if result.ammo is not None:
        faces = " ".join(_face(r, 6) for r in result.ammo.rolls)
        ah = f"+{result.ammo_hits}" if result.ammo_hits else "—"
        lines.append("")
        lines.append(f"Ammo   {len(result.ammo.rolls)}×d6   {faces}   →  {ah}")

    if result.succeeded:
        n = result.total_successes
        verdict = f"## ✅ Success — {n} success{'' if n == 1 else 'es'}"
        accent = GREEN
    else:
        verdict = "## ❌ Failure — 0 successes"
        accent = RED

    notes: list[str] = []
    if result.succeeded and result.ammo_hits:
        notes.append(f"_{result.successes} from the check + {result.ammo_hits} from ammo._")
    if result.ammo is not None:
        notes.append(f"🔫 **{result.rounds_spent}** rounds spent")
        if result.jam_ones:
            line = (
                f"🔧 **{result.jam_ones}× ⚀** — pushing this roll would cost "
                f"−{result.jam_ones} weapon reliability"
            )
            if result.jam_ones >= 2:
                line += " **and jam the weapon** 💥"
            notes.append(line)
        if not result.succeeded and result.ammo_hits:
            notes.append("_Attack missed — ammo 6s don't add hits on a miss._")
    elif result.can_push_warn:
        notes.append(
            "_A 1 is showing — careful if you push (it can hurt you or the gear used)._"
        )

    return ui.card(
        _heading(interaction, "rolled a **Twilight 2000** check"),
        ui.separator(),
        ui.text(ansi_block("\n".join(lines))),
        ui.separator(),
        ui.text(verdict),
        ui.text("\n".join(notes)) if notes else None,
        accent=accent,
    )


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
        view = _roll_card(
            interaction, result.expression, result.groups, result.modifier, result.total
        )
        await interaction.response.send_message(view=view)

    # --- Quick dice ---------------------------------------------------------

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
        view = _roll_card(interaction, f"{count}d{sides}", [group], 0, group.subtotal)
        await interaction.response.send_message(view=view)

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
        untrained = not skill or not str(skill).strip()
        view = _t2k_card(interaction, result, untrained=untrained)
        await interaction.response.send_message(view=view)

    # Ammo dice are never rolled on their own — they're always part of a ranged
    # attack — so they live on /t2k via the `ammo` option, not a separate command.


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DiceCog(bot))
