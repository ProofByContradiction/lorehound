"""Pure text rendering for equipment builds — a running summary during the flow and
the finished built-item card. Returns Markdown (no Discord/UI imports) so it's
unit-testable; the cog wraps it in a Components V2 card. Mirrors ``chargen.render``.
"""

from __future__ import annotations

from .model import RobotBuild, ShipBuild, SuitBuild

_ESC = "\x1b"


def _paint(text: str, *codes: str) -> str:
    return f"{_ESC}[{';'.join(codes)}m{text}{_ESC}[0m" if codes else text


def build_summary(draft: SuitBuild) -> str:
    """A compact running line shown above the current step, so the build takes shape as
    choices are made. Empty until a base suit is chosen."""
    if not draft.base:
        return ""
    line = f"**Base** · {draft.display}"
    if draft.slots_total:
        line += f"  ·  Slots **{draft.slots_used}/{draft.slots_total}**"
    if draft.options:
        line += f"  ·  {len(draft.options)} option" + ("s" if len(draft.options) != 1 else "")
    return line


def _stat_block(draft: SuitBuild) -> str:
    """An aligned ANSI block for the built suit's headline stats. Colour is applied after
    padding so alignment holds inside the ```ansi block."""
    prot = _paint(draft.protection or "—", "1", "32")            # green — the headline
    slots = f"{draft.slots_used} / {draft.slots_total}"
    slots_c = _paint(slots, "1", "36" if draft.slots_free else "33")  # cyan, amber when full
    lines = [
        f"{'Protection':<11}{prot}",
        f"{'STR':<11}{draft.str_mod or '—'}    {'DEX':<4}{draft.dex_mod or '—'}    "
        f"{'TL':<3}{draft.tl or '—'}",
        "",
        f"{'Slots':<11}{slots_c}  ({draft.slots_free} free)",
        f"{'Cost':<11}{draft.cost or '—'}",
    ]
    return "```ansi\n" + "\n".join(lines) + "\n```"


def built_suit_sheet(draft: SuitBuild) -> str:
    """The finished powered-armour build as Markdown."""
    lines = [f"## 🛡️ {draft.display}", f"-# {draft.game} · powered-armour build"]
    lines.append(_stat_block(draft))
    if draft.options:
        lines.append("**Installed** — " + ", ".join(draft.options))
    if draft.source:
        lines.append(f"-# Source: {draft.source}")
    return "\n".join(lines)


# --- ship builder ---------------------------------------------------------------

def ship_summary(draft: ShipBuild) -> str:
    """Running line above the current ship-build step."""
    if not draft.hull_tons:
        return ""
    parts = [f"**Hull** · {draft.hull_tons}t {draft.config}".rstrip()]
    if draft.thrust:
        parts.append(f"Thrust {draft.thrust}")
    if draft.jump:
        parts.append(f"Jump {draft.jump}")
    return "  ·  ".join(parts)


def built_ship_sheet(draft: ShipBuild) -> str:
    """The finished starship build as Markdown: a per-component tonnage/cost breakdown
    with the tonnage budget and total cost."""
    lines = [f"## 🚀 {draft.display}", f"-# {draft.game} · starship build"]
    block = [f"{label[:26]:<27}{tons:>5.0f}t   MCr {cost:>8.2f}" for label, tons, cost in draft.lines]
    block.append("─" * 46)
    tfree = draft.tonnage_free
    block.append(f"{'Tonnage used':<27}{draft.tonnage_used:>5.0f}t   ({tfree:.0f}t free of {draft.hull_tons})")
    block.append(f"{'Total cost':<27}{'':>6}   MCr {draft.total_cost:>8.2f}")
    lines.append("```\n" + "\n".join(block) + "\n```")
    if draft.warnings:
        lines.append("⚠️ " + "; ".join(draft.warnings))
    if tfree > 0.5:
        lines.append(f"-# {tfree:.0f}t unallocated")
    if draft.source:
        lines.append(f"-# Source: {draft.source}")
    return "\n".join(lines)


# --- robot builder --------------------------------------------------------------

def robot_summary(draft: RobotBuild) -> str:
    if not draft.size:
        return ""
    line = f"**Chassis** · Size {draft.size}"
    if draft.locomotion:
        line += f" {draft.locomotion}"
    if draft.slots_total:
        line += f"  ·  Slots **{draft.slots_used}/{draft.slots_total}**"
    if draft.options:
        line += f"  ·  {len(draft.options)} option" + ("s" if len(draft.options) != 1 else "")
    return line


def built_robot_sheet(draft: RobotBuild) -> str:
    """The finished robot build as Markdown."""
    lines = [f"## 🤖 {draft.display}", f"-# {draft.game} · robot build"]
    block = [
        f"{'Hits':<11}{draft.base_hits}",
        f"{'Slots':<11}{draft.slots_used} / {draft.slots_total}  ({draft.slots_free} free)",
        f"{'Cost':<11}Cr{draft.total_cost:,}",
    ]
    lines.append("```\n" + "\n".join(block) + "\n```")
    if draft.options:
        lines.append("**Installed** — " + ", ".join(draft.options))
    if draft.source:
        lines.append(f"-# Source: {draft.source}")
    return "\n".join(lines)
