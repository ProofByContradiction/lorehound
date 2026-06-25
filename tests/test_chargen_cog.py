"""Smoke tests for the /character cog view — no live Discord, AsyncMock interactions.

Exercises the interactive ChargenView transitions: the mode picker starts a session,
advancing a step edits the message and advances the session, the author gate rejects
others, and a completed session renders a sheet view without error.

Run with:  python -m unittest tests.test_chargen_cog
"""

import random
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from lorehound.careers import Career, CareerSection
from lorehound.chargen import registry
from lorehound.chargen.engine import QUICK, ChargenSession
from lorehound.chargen.model import CharacterDraft
from lorehound.chargen.t2k import t2k_flow
from lorehound.cogs import chargen_cog

GAME = "Twilight: 2000"


def _career():
    return Career(
        game=GAME, name="Combat Arms", source="Core.pdf", locator="p. 34",
        sections=[
            CareerSection(label="Requirements", text="STR or AGL B+"),
            CareerSection(label="Starting Rank", text="Private"),
            CareerSection(label="Skills", text="Ranged Combat, Recon, Close Combat"),
            CareerSection(label="Specialty (D6)",
                          rows=[["Roll (D6)", "Specialty"], ["1", "Rifleman"], ["2", "Tanker"]]),
            CareerSection(label="Starting Gear", text="✓ Assault rifle ✓ Knife"),
        ],
    )


def _bot():
    rules = SimpleNamespace(careers={GAME: {"combat arms": _career()}})
    return SimpleNamespace(rules_service=rules)


def _interaction(user_id=1):
    inter = MagicMock()
    inter.user.id = user_id
    inter.user.display_avatar.url = "http://example/avatar.png"
    inter.response = MagicMock()
    inter.response.edit_message = AsyncMock()
    inter.response.send_message = AsyncMock()
    return inter


def _run(coro):
    import asyncio
    return asyncio.run(coro)


class TestChargenView(unittest.TestCase):
    def setUp(self):
        self.system = registry.chargen_for(GAME)
        self.assertIsNotNone(self.system)
        self.bot = _bot()

    def _view(self, session):
        return chargen_cog.ChargenView(
            bot=self.bot, system=self.system, game=GAME, author_id=1, session=session,
        )

    def test_new_session_snapshots_data(self):
        session = chargen_cog._new_session(self.bot, self.system, GAME, QUICK)
        self.assertIsInstance(session, ChargenSession)
        self.assertIsNotNone(session.current)            # first step ready
        self.assertTrue(session.ctx.data.has_careers)    # data snapshotted

    def test_mode_pick_starts_session_and_edits(self):
        view = self._view(None)                          # mode-picker state
        inter = _interaction()
        _run(view._make_mode_picker(QUICK)(inter))
        inter.response.edit_message.assert_called_once()
        nxt = inter.response.edit_message.call_args.kwargs["view"]
        self.assertIsInstance(nxt, chargen_cog.ChargenView)
        self.assertIsNotNone(nxt.session)
        self.assertEqual(nxt.session.mode, QUICK)

    def test_advance_edits_and_progresses(self):
        session = chargen_cog._new_session(self.bot, self.system, GAME, QUICK)
        view = self._view(session)
        step = session.current
        inter = _interaction()
        value = step.options[0].value if step.options else None
        _run(view.advance(inter, value))
        inter.response.edit_message.assert_called_once()
        nxt = inter.response.edit_message.call_args.kwargs["view"]
        self.assertIs(nxt.session, session)              # same session, advanced
        self.assertGreaterEqual(len(session.history), 1)

    def test_author_gate_rejects_others(self):
        view = self._view(chargen_cog._new_session(self.bot, self.system, GAME, QUICK))
        intruder = _interaction(user_id=999)
        allowed = _run(view.interaction_check(intruder))
        self.assertFalse(allowed)
        intruder.response.send_message.assert_called_once()

    def test_completed_session_renders_sheet_view(self):
        # Drive a full quick-gen, then ensure the completed view builds (sheet + buttons).
        rng = random.Random(1)
        session = ChargenSession(
            t2k_flow, mode=QUICK, draft=CharacterDraft(game=GAME),
            data=chargen_cog._new_session(self.bot, self.system, GAME, QUICK).ctx.data,
            rng=rng,
        )
        for _ in range(500):
            if session.current is None:
                break
            opts = session.current.options
            session.resolve(rng.choice(opts).value if opts else None)
        self.assertTrue(session.complete)
        view = self._view(session)                       # should build without error
        self.assertIsInstance(view, chargen_cog.ChargenView)


if __name__ == "__main__":
    unittest.main()
