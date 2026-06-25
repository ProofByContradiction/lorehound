"""Unit tests for the system-agnostic chargen engine — no Discord/network.

A stub flow exercises both traversal policies: quick (auto-resolve everything but
essential choices) and faithful (pause on every step). The real T2K flow plugs the
same engine in a later phase.

Run with:  python -m unittest tests.test_chargen
"""

import random
import unittest

from lorehound.chargen.engine import FAITHFUL, QUICK, ChargenSession
from lorehound.chargen.model import CharacterDraft, Option, Step, StepKind
from lorehound.chargen.render import character_sheet, draft_summary
from lorehound.dice import RollResult


def _fixed_roller(total: int):
    return lambda spec: RollResult(expression=spec, groups=[], modifier=0, total=total)


def _stub_flow(ctx):
    """method (essential choice) → attr (roll) → info → specialty (non-essential
    choice). Fills the draft as it goes; returns when done."""
    m = yield Step("method", StepKind.CHOICE, "Method?",
                   options=[Option("lifepath", "Life Path"), Option("archetype", "Archetype")],
                   essential=True)
    ctx.draft.method = m.value
    r = yield Step("attr", StepKind.ROLL, "Roll a stat", roll_spec="2d6")
    ctx.draft.attributes["STR"] = str(r.total)
    yield Step("brief", StepKind.INFO, "The war is coming.")
    s = yield Step("spec", StepKind.CHOICE, "Pick a specialty",
                   options=[Option("sniper", "Sniper"), Option("medic", "Medic")],
                   essential=False)
    ctx.draft.specialties.append(s.detail)
    ctx.draft.name = "Stub McTest"


def _session(mode, **kw):
    return ChargenSession(
        _stub_flow, mode=mode, draft=CharacterDraft(game="Test"),
        roller=_fixed_roller(7), rng=random.Random(0), **kw,
    )


class TestQuickMode(unittest.TestCase):
    def test_pauses_only_on_essential_choice(self):
        s = _session(QUICK)
        # The first essential choice is the only thing presented up front.
        self.assertIsNotNone(s.current)
        self.assertEqual(s.current.id, "method")
        self.assertFalse(s.complete)

    def test_auto_resolves_rest_after_essential(self):
        s = _session(QUICK)
        nxt = s.resolve("lifepath")
        # After the one essential choice, quick mode rolls/acks/auto-picks to the end.
        self.assertIsNone(nxt)
        self.assertTrue(s.complete)
        self.assertEqual(s.draft.method, "lifepath")
        self.assertEqual(s.draft.attributes["STR"], "7")   # injected roll total
        self.assertEqual(len(s.draft.specialties), 1)       # auto-picked
        self.assertIn(s.draft.specialties[0], ("Sniper", "Medic"))
        self.assertEqual(s.draft.name, "Stub McTest")


class TestFaithfulMode(unittest.TestCase):
    def test_pauses_on_every_step(self):
        s = _session(FAITHFUL)
        seen = [s.current.id]
        self.assertEqual(s.current.id, "method")
        self.assertEqual(s.resolve("archetype").id, "attr")   # roll step, paused
        seen.append("attr")
        self.assertEqual(s.resolve(None).id, "brief")          # info step, paused
        self.assertEqual(s.current.kind, StepKind.INFO)
        self.assertEqual(s.resolve(None).id, "spec")           # final choice, paused
        self.assertIsNone(s.resolve("medic"))                  # done
        self.assertTrue(s.complete)
        self.assertEqual(s.draft.method, "archetype")
        self.assertEqual(s.draft.attributes["STR"], "7")
        self.assertEqual(s.draft.specialties, ["Medic"])

    def test_invalid_choice_is_noop(self):
        s = _session(FAITHFUL)
        same = s.resolve("not-an-option")
        self.assertEqual(same.id, "method")     # still awaiting a valid pick
        self.assertFalse(s.complete)


class TestModeGuard(unittest.TestCase):
    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            ChargenSession(_stub_flow, mode="turbo", draft=CharacterDraft(game="Test"))


class TestRender(unittest.TestCase):
    def test_sheet_includes_filled_fields(self):
        s = _session(QUICK)
        s.resolve("lifepath")
        sheet = character_sheet(s.draft)
        self.assertIn("Stub McTest", sheet)
        self.assertIn("STR", sheet)
        self.assertIn("Test", sheet)            # game name

    def test_summary_reflects_progress(self):
        draft = CharacterDraft(game="Test", attributes={"STR": "B"})
        self.assertIn("STR", draft_summary(draft))
        self.assertEqual(draft_summary(CharacterDraft(game="Test")), "")  # nothing yet


class TestT2KData(unittest.TestCase):
    """The T2K data accessor flattens a detected career card into a typed T2KCareer
    and snapshots the set for a session."""

    def _career(self, name, sections):
        from lorehound.careers import Career, CareerSection
        return Career(
            game="Twilight: 2000", name=name, source="Core.pdf", locator="p. 34",
            sections=[CareerSection(**s) for s in sections],
        )

    def test_flatten_military_career(self):
        from lorehound.chargen.data import t2k_career_from
        c = self._career("Combat Arms", [
            {"label": "Requirements", "text": "STR or AGL B+"},
            {"label": "Starting Rank", "text": "Private"},
            {"label": "Skills", "text": "Close Combat, Heavy Weapons, Ranged Combat"},
            {"label": "Specialty (D6)", "rows": [["Roll (D6)", "Specialty"],
                                                 ["1", "Rifleman"], ["2", "Tanker"]]},
            {"label": "Starting Gear", "text": "✓ Assault rifle ✓ D6 reloads ✓ Knife"},
        ])
        tc = t2k_career_from(c)
        self.assertTrue(tc.is_military)
        self.assertEqual(tc.rank, "Private")
        self.assertEqual(tc.skills, ["Close Combat", "Heavy Weapons", "Ranged Combat"])
        self.assertEqual(tc.specialties, [("1", "Rifleman"), ("2", "Tanker")])
        self.assertEqual(tc.gear, ["Assault rifle", "D6 reloads", "Knife"])
        self.assertEqual(tc.requirements, "STR or AGL B+")

    def test_civilian_has_no_rank(self):
        from lorehound.chargen.data import t2k_career_from
        c = self._career("Doctor", [
            {"label": "Skills", "text": "Medical Aid, Persuasion"},
            {"label": "Specialty (D6)", "rows": [["Roll (D6)", "Specialty"], ["1", "Combat Medic"]]},
        ])
        tc = t2k_career_from(c)
        self.assertFalse(tc.is_military)
        self.assertEqual(tc.rank, "")

    def test_build_snapshot_filters_and_orders(self):
        from lorehound.chargen.data import build_t2k_data
        empty = self._career("Empty", [{"label": "Flavour", "text": "no usable data"}])
        civ = self._career("Doctor", [{"label": "Skills", "text": "Medical Aid"}])
        mil = self._career("Combat Arms", [
            {"label": "Starting Rank", "text": "Private"},
            {"label": "Skills", "text": "Ranged Combat"},
        ])

        class _Rules:
            careers = {"Twilight: 2000": {"empty": empty, "doctor": civ, "combat arms": mil}}

        data = build_t2k_data(_Rules(), "Twilight: 2000")
        names = [c.name for c in data.careers]
        self.assertEqual(names, ["Combat Arms", "Doctor"])   # military first; Empty dropped
        self.assertIsNotNone(data.career("doctor"))


if __name__ == "__main__":
    unittest.main()
