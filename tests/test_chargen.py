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


class TestBack(unittest.TestCase):
    def test_cannot_back_from_first_step(self):
        s = _session(FAITHFUL)
        self.assertEqual(s.current.id, "method")
        self.assertFalse(s.can_back)
        self.assertEqual(s.back().id, "method")    # no-op

    def test_back_replays_earlier_answers_and_undoes_later(self):
        s = _session(FAITHFUL)            # method → attr(roll) → brief → spec
        s.resolve("lifepath")             # now at the attr roll
        s.resolve(None)                   # roll attr (fixed 7) → at brief
        self.assertEqual(s.current.id, "brief")
        self.assertEqual(s.draft.attributes.get("STR"), "7")
        self.assertTrue(s.can_back)

        back = s.back()                   # rewind to the attr roll
        self.assertEqual(back.id, "attr")
        self.assertEqual(s.draft.method, "lifepath")   # earlier choice replayed
        self.assertNotIn("STR", s.draft.attributes)     # the later roll was undone

        s.resolve(None)                   # re-roll the attr (deterministic: 7)
        self.assertEqual(s.current.id, "brief")
        self.assertEqual(s.draft.attributes.get("STR"), "7")


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

    def test_sheet_has_ansi_stat_block(self):
        draft = CharacterDraft(
            game="Test", attributes={"STR": "B", "AGL": "C", "INT": "A", "EMP": "D"},
            derived={"Hit Capacity": "5"},
        )
        sheet = character_sheet(draft)
        self.assertIn("```ansi", sheet)       # attributes render as a colour block
        self.assertIn("STR", sheet)
        self.assertIn("Hit", sheet)           # derived abbreviated in the block


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


class TestT2KFlow(unittest.TestCase):
    """The T2K life-path flow, driven end-to-end over synthetic career data."""

    def _data(self, *, single=False, childhood=False):
        from lorehound.chargen.data import T2KCareer, T2KData
        combat = T2KCareer(
            name="Combat Arms", rank="Private",
            skills=["Ranged Combat", "Recon", "Close Combat"],
            specialties=[("1", "Rifleman"), ("2", "Tanker")], gear=["Assault rifle", "Knife"],
        )
        ch = [("Street Kid", ["Close Combat", "Recon", "Mobility"])] if childhood else []
        if single:
            return T2KData(game="Twilight: 2000", careers=[combat], childhood=ch)
        doctor = T2KCareer(
            name="Doctor", skills=["Medical Aid", "Persuasion"],
            specialties=[("1", "Combat Medic")], gear=["Medkit"],
        )
        return T2KData(game="Twilight: 2000", careers=[combat, doctor], childhood=ch)

    def _drive(self, mode, data, seed=3):
        from lorehound.chargen.engine import ChargenSession
        from lorehound.chargen.model import CharacterDraft
        from lorehound.chargen.t2k import t2k_flow

        rng = random.Random(seed)
        s = ChargenSession(
            t2k_flow, mode=mode, draft=CharacterDraft(game="Twilight: 2000"), data=data, rng=rng,
        )
        prompts = 0
        for _ in range(500):  # safety cap against a non-advancing flow
            if s.current is None:
                break
            prompts += 1
            value = rng.choice(s.current.options).value if s.current.options else None
            s.resolve(value)
        return s, prompts

    def test_quick_run_produces_valid_character(self):
        from lorehound.chargen.engine import QUICK
        s, _ = self._drive(QUICK, self._data())
        d = s.draft
        self.assertTrue(s.complete)
        self.assertEqual(d.method, "Life Path")
        self.assertEqual(set(d.attributes), {"STR", "AGL", "INT", "EMP"})
        self.assertTrue(all(v in ("A", "B", "C", "D") for v in d.attributes.values()))
        self.assertIn("Hit Capacity", d.derived)
        self.assertIn("Stress Capacity", d.derived)
        self.assertTrue(d.career_history)          # at least one term
        self.assertIn("Nationality", d.notes)

    def test_faithful_prompts_more_than_quick(self):
        from lorehound.chargen.engine import FAITHFUL, QUICK
        _, quick_prompts = self._drive(QUICK, self._data())
        _, faithful_prompts = self._drive(FAITHFUL, self._data())
        self.assertGreater(faithful_prompts, quick_prompts)

    def test_first_term_trains_ranged_combat(self):
        from lorehound.chargen.engine import FAITHFUL
        s, _ = self._drive(FAITHFUL, self._data(single=True))
        # The first term must train Ranged Combat when the career offers it.
        self.assertIn("Ranged Combat", s.draft.skills)

    def test_no_data_completes_with_note(self):
        from lorehound.chargen.data import T2KData
        from lorehound.chargen.engine import QUICK
        s, _ = self._drive(QUICK, T2KData(game="Twilight: 2000", careers=[]))
        self.assertTrue(s.complete)                # graceful, no crash
        self.assertIn("Note", s.draft.notes)

    def test_attributes_use_2d3_increases_from_c_baseline(self):
        from lorehound.chargen.engine import QUICK, ChargenSession
        from lorehound.chargen.model import CharacterDraft
        from lorehound.chargen.t2k import t2k_flow

        rng = random.Random(5)
        # Force the 2D3 attribute-increase roll to 4 (the only ROLL in the flow).
        s = ChargenSession(
            t2k_flow, mode=QUICK, draft=CharacterDraft(game="Twilight: 2000"),
            data=self._data(),
            roller=lambda spec: RollResult(expression=spec, groups=[], modifier=0, total=4),
            rng=rng,
        )
        for _ in range(500):
            if s.current is None:
                break
            opts = s.current.options
            s.resolve(rng.choice(opts).value if opts else None)

        ladder = ["D", "C", "B", "A"]
        # Every attribute started at C and only moved up; the total steps above C
        # must equal the rolled 2D3 result.
        steps_up = sum(ladder.index(v) - ladder.index("C") for v in s.draft.attributes.values())
        self.assertEqual(steps_up, 4)
        self.assertTrue(all(ladder.index(v) >= ladder.index("C") for v in s.draft.attributes.values()))
        self.assertEqual(s.draft.derived["Coolness Under Fire"], "D")


    def test_childhood_trains_a_class_skill(self):
        from lorehound.chargen.engine import FAITHFUL
        s, _ = self._drive(FAITHFUL, self._data(single=True, childhood=True))
        self.assertEqual(s.draft.notes.get("Childhood"), "Street Kid")
        self.assertTrue(
            any(sk in s.draft.skills for sk in ("Close Combat", "Recon", "Mobility"))
        )

    def _drive_with_roll(self, total):
        from lorehound.chargen.engine import QUICK, ChargenSession
        from lorehound.chargen.model import CharacterDraft
        from lorehound.chargen.t2k import t2k_flow
        rng = random.Random(2)
        s = ChargenSession(
            t2k_flow, mode=QUICK, draft=CharacterDraft(game="Twilight: 2000"),
            data=self._data(),
            roller=lambda spec: RollResult(expression=spec, groups=[], modifier=0, total=total),
            rng=rng,
        )
        for _ in range(500):
            if s.current is None:
                break
            opts = s.current.options
            s.resolve(rng.choice(opts).value if opts else None)
        return s.draft

    def test_specialty_requires_passing_skill_roll(self):
        # Every skill roll fails (1 < 6): no specialty is earned in any term.
        self.assertEqual(self._drive_with_roll(1).specialties, [])

    def test_specialty_earned_when_roll_succeeds(self):
        # Every skill roll succeeds (12): at least one specialty is earned.
        self.assertTrue(self._drive_with_roll(12).specialties)

    def test_age_is_tracked(self):
        from lorehound.chargen.engine import QUICK
        s, _ = self._drive(QUICK, self._data())
        age = int(s.draft.notes["Age"])
        self.assertGreaterEqual(age, 18)              # starts at 18
        if s.draft.career_history:
            self.assertGreater(age, 18)               # each served term ages you


class TestT2KProse(unittest.TestCase):
    """The childhood D6 table is parsed from the book's prose (blank-line-delimited
    skill triples), not from a structured table."""

    SAMPLE = (
        "blah CHILDHOOD**\n\n**D6** **1. STREET KID** **2. SMALL TOWN** **3. WORKING** "
        "**4. INTELLECTUAL** **5. MILITARY** **6. AFFLUENCE**\n**CLASS** **FAMILY**\n\n\n\n"
        "**SKILLS** Close Combat,\nMobility,\nRecon\n\n\n\n"
        "Driving, Ranged\nCombat,\nSurvival\n\n\n\n"
        "Close Combat,\nStamina,\nTech\n\n\n\n"
        "Tech,\nMedical Aid,\nPersuasion\n\n\n\n"
        "Stamina,\nMobility,\nRanged Combat\n\n\n\n"
        "Mobility,\nCommand,\nPersuasion\n\n\n\n## MILITARY SERVICE\nMore prose…"
    )

    def test_parses_six_classes_with_skills(self):
        from lorehound.chargen.t2k_prose import parse_childhood
        got = parse_childhood(self.SAMPLE)
        names = [c for c, _ in got]
        self.assertEqual(
            names, ["Street Kid", "Small Town", "Working", "Intellectual", "Military", "Affluence"]
        )
        self.assertEqual(dict(got)["Street Kid"], ["Close Combat", "Mobility", "Recon"])
        self.assertEqual(dict(got)["Small Town"], ["Driving", "Ranged Combat", "Survival"])

    def test_missing_block_returns_empty(self):
        from lorehound.chargen.t2k_prose import extract_t2k_prose, parse_childhood
        self.assertEqual(parse_childhood("no childhood here"), [])
        self.assertEqual(extract_t2k_prose("nothing relevant"), {})


class TestRegistration(unittest.TestCase):
    def test_t2k_system_registered(self):
        from lorehound.chargen import registry
        sc = registry.chargen_for("Twilight: 2000")
        self.assertIsNotNone(sc)
        self.assertTrue(sc.matches("T2K Core"))
        self.assertIsNone(registry.chargen_for("Call of Cthulhu"))


if __name__ == "__main__":
    unittest.main()
