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
        # Force the 2D3 attribute-increase roll to 4 (the only ROLL before careers).
        s = ChargenSession(
            t2k_flow, mode=QUICK, draft=CharacterDraft(game="Twilight: 2000"),
            data=self._data(),
            roller=lambda spec: RollResult(expression=spec, groups=[], modifier=0, total=4),
            rng=rng,
        )
        # Resolve only the attribute steps; stop once career terms begin (aging there
        # can later lower an attribute, which is a separate mechanic).
        for _ in range(50):
            if s.current is None or s.current.id.startswith("career"):
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

    def test_war_ends_loop_and_age_effect_fires(self):
        # Every D8 comes up 1: term 2's age-effect (1 < 2 terms) drops an attribute
        # and the war check (1 < 2) ends the career loop after two terms.
        s = self._drive_with_roll(1)
        self.assertEqual(s.notes["Terms served"], "2")
        self.assertTrue(any("Age effect" in line for line in s.log))

    def test_two_skill_picks_in_a_term_are_distinct(self):
        from lorehound.chargen.data import T2KCareer, T2KData
        from lorehound.chargen.engine import FAITHFUL, ChargenSession
        from lorehound.chargen.model import CharacterDraft
        from lorehound.chargen.t2k import t2k_flow

        career = T2KCareer(name="Scavenger", skills=["Alpha", "Beta", "Gamma"],
                           specialties=[("1", "Spec")])
        data = T2KData(game="Twilight: 2000", careers=[career])
        rng = random.Random(1)
        s = ChargenSession(t2k_flow, mode=FAITHFUL,
                           draft=CharacterDraft(game="Twilight: 2000"), data=data, rng=rng)
        first, checked = None, False
        for _ in range(500):
            if s.current is None:
                break
            cur = s.current
            if cur.id == "skill_1_0":
                first = cur.options[0].value
                s.resolve(first)
                continue
            if cur.id == "skill_1_1":
                self.assertNotIn(first, [o.value for o in cur.options])  # can't re-pick
                checked = True
                s.resolve(cur.options[0].value)
                continue
            s.resolve(rng.choice(cur.options).value if cur.options else None)
        self.assertTrue(checked, "never reached the second skill pick of term 1")

    def test_advancement_promotes_rank_and_raises_cuf(self):
        from lorehound.chargen.data import T2KCareer, T2KData
        from lorehound.chargen.engine import FAITHFUL, ChargenSession
        from lorehound.chargen.model import CharacterDraft
        from lorehound.chargen.t2k import t2k_flow

        ranks = {"columns": ["us", "soviet", "polish", "swedish"], "rows": [
            ["Private", "Ryadovoy", "Szeregowy", "Menig"],
            ["Private First Class", "–", "Starszy szeregowy", "–"],
            ["Corporal", "Efreitor", "Kapral", "Korpral"],
            ["Sergeant", "Mladshiy", "Plutonowy", "Furir"],
        ]}
        career = T2KCareer(name="Combat Arms", rank="Private", skills=["Ranged Combat", "Recon"],
                           specialties=[("1", "Rifleman"), ("2", "Tanker"), ("3", "Sniper")])
        data = T2KData(game="Twilight: 2000", careers=[career], ranks=ranks)
        rng = random.Random(0)
        s = ChargenSession(
            t2k_flow, mode=FAITHFUL, draft=CharacterDraft(game="Twilight: 2000"), data=data,
            roller=lambda spec: RollResult(expression=spec, groups=[], modifier=0, total=6), rng=rng,
        )
        for _ in range(800):
            if s.current is None:
                break
            if s.current.id == "nationality":
                s.resolve("us")
                continue
            opts = s.current.options
            s.resolve(opts[0].value if opts else None)
        d = s.draft
        # Every advancement roll succeeds → promotions climb the ladder and lift CUF off D.
        self.assertEqual(d.rank, "Sergeant")                  # capped at the ladder top
        self.assertEqual(d.derived["Coolness Under Fire"], "A")
        self.assertTrue(any("Promoted to" in line for line in d.log))

    def test_specialties_never_duplicate(self):
        d = self._drive_with_roll(12)        # always succeeds → grabs specialties every term
        self.assertEqual(len(d.specialties), len(set(d.specialties)))

    def test_ineligible_career_is_not_offered(self):
        from lorehound.chargen.data import T2KCareer, T2KData
        from lorehound.chargen.engine import QUICK, ChargenSession
        from lorehound.chargen.model import CharacterDraft
        from lorehound.chargen.t2k import t2k_flow

        locked = T2KCareer(name="Locked", requirements="at least one term in Ghost Unit",
                           skills=["Alpha"], specialties=[("1", "S")])
        openc = T2KCareer(name="Open", requirements="None", skills=["Beta"], specialties=[("1", "S")])
        data = T2KData(game="Twilight: 2000", careers=[locked, openc])
        rng = random.Random(0)
        s = ChargenSession(t2k_flow, mode=QUICK,
                           draft=CharacterDraft(game="Twilight: 2000"), data=data, rng=rng)
        seen = False
        for _ in range(200):
            if s.current is None:
                break
            if s.current.id == "career_1":
                values = [o.value for o in s.current.options]
                self.assertIn("Open", values)
                self.assertNotIn("Locked", values)   # needs a prior term it can't have yet
                seen = True
            opts = s.current.options
            s.resolve(rng.choice(opts).value if opts else None)
        self.assertTrue(seen)


class TestRequirementEnforcement(unittest.TestCase):
    """Parsing freeform requirement text into enforceable predicates."""

    def _data(self, careers):
        from lorehound.chargen.data import T2KData
        return T2KData(game="Twilight: 2000", careers=careers)

    def _career(self, req):
        from lorehound.chargen.data import T2KCareer
        return T2KCareer(name="X", requirements=req, skills=["Skill"])

    def _eligible(self, req, attrs, history):
        data = self._data([self._career(req)])
        return data.eligibility(data.career("X"), attrs, history)

    ALL_C = {"STR": "C", "AGL": "C", "INT": "C", "EMP": "C"}

    def test_blank_requirements_always_eligible(self):
        for blank in ("", "None", "–"):
            self.assertTrue(self._eligible(blank, self.ALL_C, [])[0])

    def test_attribute_threshold_and_or(self):
        self.assertFalse(self._eligible("STR and AGL B+", self.ALL_C, [])[0])
        self.assertTrue(self._eligible("STR and AGL B+", {**self.ALL_C, "STR": "B", "AGL": "B"}, [])[0])
        # OR only needs one of the two.
        self.assertTrue(self._eligible("STR or AGL B+", {**self.ALL_C, "AGL": "A"}, [])[0])
        self.assertFalse(self._eligible("STR or AGL B+", self.ALL_C, [])[0])

    def test_no_d_attribute(self):
        self.assertTrue(self._eligible("no D attribute", self.ALL_C, [])[0])
        self.assertFalse(self._eligible("no D attribute", {**self.ALL_C, "EMP": "D"}, [])[0])

    def test_specific_attribute_levels(self):
        self.assertTrue(self._eligible("EMP C or D", {**self.ALL_C, "EMP": "D"}, [])[0])
        self.assertFalse(self._eligible("EMP C or D", {**self.ALL_C, "EMP": "B"}, [])[0])

    def test_history_term_prerequisite(self):
        self.assertFalse(self._eligible("at least one term in Combat Arms", self.ALL_C, [])[0])
        self.assertTrue(self._eligible("at least one term in Combat Arms", self.ALL_C,
                                       ["Combat Arms (Rifleman)"])[0])

    def test_no_terms_in_prerequisite(self):
        self.assertTrue(self._eligible("no terms in prison", self.ALL_C, ["Farmer"])[0])
        self.assertFalse(self._eligible("no terms in prison", self.ALL_C, ["Prisoner"])[0])

    def test_military_and_education_categories(self):
        from lorehound.chargen.data import T2KCareer
        marine = T2KCareer(name="Combat Arms", rank="Private", skills=["Ranged Combat"])
        sciences = T2KCareer(name="Sciences", skills=["Tech"])
        needs_mil = T2KCareer(name="Vet", requirements="one or more terms in the military", skills=["S"])
        needs_edu = T2KCareer(name="Agent", requirements="at least one term in Education", skills=["S"])
        data = self._data([marine, sciences, needs_mil, needs_edu])
        self.assertFalse(data.eligibility(needs_mil, self.ALL_C, ["Sciences"])[0])
        self.assertTrue(data.eligibility(needs_mil, self.ALL_C, ["Combat Arms"])[0])
        self.assertFalse(data.eligibility(needs_edu, self.ALL_C, ["Combat Arms"])[0])
        self.assertTrue(data.eligibility(needs_edu, self.ALL_C, ["Sciences"])[0])

    def test_unparseable_clause_is_advisory_not_blocking(self):
        # The clause we can't parse must not block; the parseable one still does.
        ok, unmet = self._eligible("requirements for the functional area", self.ALL_C, [])
        self.assertTrue(ok)
        self.assertFalse(self._eligible("INT B+, requirements for the functional area",
                                        self.ALL_C, [])[0])


class TestRankLadder(unittest.TestCase):
    def _ranks_table(self):
        return {"page": 17, "title": "Military Ranks", "rows": [
            ["US", "SOVIET", "POLISH", "SWEDISH"],
            ["Private", "Ryadovoy", "Szeregowy", "Menig"],
            ["Private First Class", "–", "Starszy szeregowy", "–"],
            ["Corporal / Specialist", "Efreitor", "Kapral", "Korpral"],
            ["Sergeant", "Mladshiy Serzhant", "Plutonowy", "Furir"],
            ["Staff Sergeant", "Serzhant", "Sierżant", "Sergeant"],
            ["Sergeant First Class", "Starshiy Serzhant", "Starszy sierżant", "–"],
            ["Second Lieutenant", "Mladshiy Leytenant", "Podporucznik", "Fänrik"],
        ]}

    def test_parse_ranks_reads_the_ladder(self):
        from lorehound.chargen.t2k_prose import parse_ranks
        got = parse_ranks([{"rows": [["x", "y"]]}, self._ranks_table()])
        self.assertEqual(got["columns"], ["us", "soviet", "polish", "swedish"])
        self.assertEqual(got["rows"][0], ["Private", "Ryadovoy", "Szeregowy", "Menig"])

    def test_parse_ranks_missing_returns_empty(self):
        from lorehound.chargen.t2k_prose import parse_ranks
        self.assertEqual(parse_ranks([]), {})
        self.assertEqual(parse_ranks([{"rows": [["A", "B", "C", "D"]]}]), {})

    def test_rank_name_localizes_with_spine_fallback(self):
        from lorehound.chargen.data import T2KData
        from lorehound.chargen.t2k_prose import parse_ranks
        data = T2KData(game="Twilight: 2000", ranks=parse_ranks([self._ranks_table()]))
        self.assertEqual(data.rank_levels()[3], "Sergeant")
        self.assertEqual(data.rank_name("soviet", 3), "Mladshiy Serzhant")
        # Soviet has no PFC ("–") → fall back to the spine label.
        self.assertEqual(data.rank_name("soviet", 1), "Private First Class")


class TestRollLine(unittest.TestCase):
    def test_shows_most_recent_roll(self):
        from lorehound.chargen.model import StepResult
        from lorehound.chargen.render import last_roll_line
        history = [
            StepResult("a", value="x", detail="x"),                 # a choice (no total)
            StepResult("r", value="2d3 [1, 2]", total=3, detail="2d3 [1, 2]"),
            StepResult("c", value="picked", detail="Picked"),       # later choice
        ]
        line = last_roll_line(history)
        self.assertIn("3", line)
        self.assertIn("2d3", line)

    def test_empty_when_no_roll(self):
        from lorehound.chargen.render import last_roll_line
        self.assertEqual(last_roll_line([]), "")


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
