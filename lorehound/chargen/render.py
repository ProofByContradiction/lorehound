"""Pure text rendering for chargen — a running summary during the flow and the
final character sheet. Returns Markdown strings (no Discord/UI imports) so it's
unit-testable in isolation; the cog wraps the strings in Components V2 cards.
"""

from __future__ import annotations

from .model import CharacterDraft, Step, StepKind

# Raw ANSI (kept local so this module stays Discord-free and unit-testable). Only
# renders inside a ```ansi block — see _stat_block.
_ESC = "\x1b"
_RATING_COLOUR = {"A": "32", "B": "36", "C": "33", "D": "31"}  # green/cyan/yellow/red
_DERIVED_SHORT = {
    "Hit Capacity": "Hit", "Stress Capacity": "Stress", "Coolness Under Fire": "CUF",
}


def _paint(text: str, *codes: str) -> str:
    return f"{_ESC}[{';'.join(codes)}m{text}{_ESC}[0m" if codes else text


def _rating(value: str) -> str:
    return _paint(value, "1", _RATING_COLOUR.get(value, "37"))


def _stat_block(draft: CharacterDraft) -> str:
    """An aligned, colour-coded ANSI block for the four attributes + derived stats.
    Colour is applied after padding the plain text, so alignment holds."""
    lines = [f"{name:<5}{_rating(draft.attributes.get(name, '-'))}"
             for name in ("STR", "AGL", "INT", "EMP")]
    if draft.derived:
        lines.append("")
        lines += [f"{_DERIVED_SHORT.get(k, k):<8}{v}" for k, v in draft.derived.items()]
    return "```ansi\n" + "\n".join(lines) + "\n```"


def _attr_line(attributes: dict[str, str]) -> str:
    if not attributes:
        return "_not set_"
    return "  ".join(f"**{k}** {v}" for k, v in attributes.items())


def draft_summary(draft: CharacterDraft) -> str:
    """A compact running summary shown above the current step, so the player always
    sees the character taking shape."""
    parts: list[str] = []
    if draft.attributes:
        parts.append(f"**Attributes** · {_attr_line(draft.attributes)}")
    if draft.career_history:
        parts.append(f"**Career** · {' → '.join(draft.career_history)}")
    if draft.skills:
        top = ", ".join(f"{k} {v}" for k, v in list(draft.skills.items())[:8])
        more = "" if len(draft.skills) <= 8 else f" (+{len(draft.skills) - 8})"
        parts.append(f"**Skills** · {top}{more}")
    if draft.specialties:
        parts.append(f"**Specialties** · {', '.join(draft.specialties)}")
    return "\n".join(parts)


def last_roll_line(history: list) -> str:
    """A one-line recap of the most recent dice roll, so step-by-step shows what you
    actually rolled (not just its downstream effect). ``""`` until the first roll."""
    for result in reversed(history):
        if result.total is not None:
            shown = result.detail or result.value
            tail = f" `{shown}`" if shown and shown != str(result.total) else ""
            return f"🎲 Last roll:{tail} → **{result.total}**"
    return ""


def step_prompt(step: Step) -> str:
    """The heading + body shown for the step awaiting the user."""
    lines = [f"### {step.prompt}"]
    if step.detail:
        lines.append(step.detail)
    if step.kind == StepKind.ROLL and step.roll_spec:
        lines.append(f"-# Roll: `{step.roll_spec}`")
    return "\n".join(lines)


def character_sheet(draft: CharacterDraft) -> str:
    """The finished character sheet as Markdown."""
    title = draft.name or "Character"
    lines = [f"## {title}", f"-# {draft.game}"]
    if draft.method:
        lines.append(f"-# Built via {draft.method}")
    if draft.attributes:
        lines.append(_stat_block(draft))
    if draft.rank:
        lines.append(f"**Rank** — {draft.rank}")
    if draft.career_history:
        lines.append(f"**Career** — {' → '.join(draft.career_history)}")
    if draft.skills:
        lines.append("")
        lines.append("**Skills**")
        lines.append(", ".join(f"{k} {v}" for k, v in draft.skills.items()))
    if draft.specialties:
        lines.append("")
        lines.append("**Specialties** — " + ", ".join(draft.specialties))
    if draft.gear:
        lines.append("")
        lines.append("**Gear** — " + ", ".join(draft.gear))
    if draft.notes:
        lines.append("")
        lines.append("\n".join(f"**{k}** — {v}" for k, v in draft.notes.items()))
    return "\n".join(lines)
