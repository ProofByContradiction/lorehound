"""Shared output toolkit: Components V2 *cards* + ANSI color.

Lorehound renders every response as a Components V2 card — a ``Container`` you
compose from text / section / separator blocks — instead of a plain embed, so the
layout is ours to control. Colored, aligned text comes from ANSI code blocks,
which are the only way Discord renders color in message text.

Usage::

    view = card(
        header(f"### 🎲 **{name}** rolled `2d6+1`", icon_url=avatar),
        separator(),
        text(ansi_block("...aligned, colored body...")),
        separator(large=True),
        text("## ✨ Total: 8"),
        accent=discord.Colour.green(),
    )
    await interaction.response.send_message(view=view)

A card built this way carries no interactive components, so it is display-only;
add an ``ActionRow``/``Section`` accessory when a card needs buttons.
"""

from __future__ import annotations

import discord

# --- ANSI ------------------------------------------------------------------
# Discord only renders these SGR codes inside a ```ansi fenced block. Padding
# must be applied to the *plain* string before painting, or the escape bytes
# throw off monospace alignment.

_ESC = "\x1b"
_RESET = f"{_ESC}[0m"


class Ansi:
    """SGR codes Discord's client understands inside ```ansi blocks."""

    GRAY = "30"
    RED = "31"
    GREEN = "32"
    YELLOW = "33"
    BLUE = "34"
    PINK = "35"
    CYAN = "36"
    WHITE = "37"
    BOLD = "1"
    UNDERLINE = "4"


def paint(s: str, *codes: str) -> str:
    """Wrap ``s`` in ANSI codes, e.g. ``paint("SUCCESS", Ansi.BOLD, Ansi.GREEN)``.

    No-op when no codes are given. Only visible inside :func:`ansi_block`.
    """
    if not codes:
        return s
    return f"{_ESC}[{';'.join(codes)}m{s}{_RESET}"


def ansi_block(body: str) -> str:
    """Fence ``body`` as an ```ansi block (where :func:`paint` colors render)."""
    return f"```ansi\n{body}\n```"


# --- Components V2 building blocks ------------------------------------------


def text(content: str) -> discord.ui.TextDisplay:
    """A block of markdown/ANSI text."""
    return discord.ui.TextDisplay(content)


def separator(*, large: bool = False, visible: bool = True) -> discord.ui.Separator:
    """A divider. ``large`` adds more vertical space; ``visible=False`` is a pure
    spacer with no line."""
    spacing = (
        discord.SeparatorSpacing.large if large else discord.SeparatorSpacing.small
    )
    return discord.ui.Separator(spacing=spacing, visible=visible)


def header(title: str, *, icon_url: str | None = None) -> discord.ui.Item:
    """A title row. With ``icon_url`` it's a Section with the icon as a right-side
    thumbnail (e.g. the roller's avatar); otherwise a plain text block."""
    td = discord.ui.TextDisplay(title)
    if icon_url:
        return discord.ui.Section(td, accessory=discord.ui.Thumbnail(icon_url))
    return td


def card(
    *items: discord.ui.Item | None,
    accent: discord.Colour | int | None = None,
    timeout: float | None = None,
) -> discord.ui.LayoutView:
    """A Components V2 card: a ``LayoutView`` holding one accent-colored
    ``Container``. ``None`` items are skipped so callers can inline conditionals."""
    view = discord.ui.LayoutView(timeout=timeout)
    container = discord.ui.Container(accent_colour=accent)
    for item in items:
        if item is not None:
            container.add_item(item)
    view.add_item(container)
    return view
