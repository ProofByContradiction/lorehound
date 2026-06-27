"""Twilight 2000 (4E) life-path character generation.

Expressed as a flow generator (see :mod:`lorehound.chargen.engine`): it yields the
ordered steps of a T2K life path and is sent the resolved answers back, reading the
career/skill/specialty/gear options from the snapshotted :class:`T2KData`.

Only the generic *algorithm* and non-copyrightable mechanics live here — the A–D die
ladder (already in :mod:`lorehound.twilight`), the Hit/Stress Capacity formulas, and
the term loop. Career data comes from the index. A few allocation amounts (attribute
increases, skill bumps per term) are tunable constants grounded in the rulebook's own
worked example and surfaced as approximate, since the exact array isn't recoverable
from the extracted tables and isn't ours to embed verbatim.

v1 covers the Life Path method (Archetype needs pre-built cards that aren't in the
index). Promotion ladders and mechanical aging are out of scope — we don't fabricate
mechanics we can't source — so the sheet records the starting rank and terms served.
"""

from __future__ import annotations

import math

from ..twilight import SUCCESS_THRESHOLD, rating_to_sides
from .data import T2KData, build_t2k_data
from .model import Option, Step, StepKind
from .registry import SystemChargen, register
from .t2k_prose import extract_t2k_prose

ATTRIBUTES = ("STR", "AGL", "INT", "EMP")
_ATTR_LADDER = ["D", "C", "B", "A"]    # ascending; A is best
_SKILL_LADDER = ["F", "D", "C", "B", "A"]  # F = untrained

# --- Allocation amounts ----------------------------------------------------
# Attributes follow the core book's rules-as-written (confirmed against the worked
# example: a 2D3 roll of 4 yields STR B, AGL A, INT B, EMP C from a C baseline):
BASELINE_ATTR = "C"          # every attribute starts at C ("average")
ATTR_INCREASE_DICE = "2d3"   # number of one-step attribute increases to distribute
CUF_START = "D"              # Coolness Under Fire starts at D
# Per-term skill handling is still simplified (see _FIDELITY_NOTE):
SKILLS_PER_TERM = 2          # one-step skill raises granted each term
RANGED_COMBAT = "Ranged Combat"  # first term must train this (T2K life-path rule)
STARTING_AGE = 18            # characters begin the life path at 18 (core book)
AGE_DICE = "1d6"             # years added per term
AGE_EFFECT_DICE = "1d8"      # each term (from the 2nd): D8 under #terms → lose a step
WAR_DICE = "1d8"             # each term (from the 2nd): D8 under #terms → WWIII begins
TERM_HARD_CAP = 10           # safety bound; the war check almost always ends it sooner

_FIDELITY_NOTE = (
    "Built rules-as-written: nationality sets your languages and rank ladder; attributes "
    "start at C with 2D3 increases (CUF starts D); childhood trains a skill and rolls a "
    "bonus specialty; career requirements are enforced; each term's advancement roll (6+) "
    "earns a specialty AND a promotion — stepping your nationality's rank ladder and raising "
    "CUF a step; aging uses D6/term with the D8-vs-terms age effect and war trigger. "
    "Approximations: the advancement roll uses your best die (the skill→attribute pairing "
    "isn't in our data), and the Archetype (quick-build) method isn't built — confirm those "
    "against your table."
)


def _step_up(rating: str, ladder: list[str]) -> str:
    """One step up the ladder toward A, capped at A."""
    try:
        i = ladder.index(rating)
    except ValueError:
        i = 0
    return ladder[min(i + 1, len(ladder) - 1)]


def _step_down(rating: str, ladder: list[str]) -> str:
    """One step down the ladder toward the floor (D for attributes)."""
    try:
        i = ladder.index(rating)
    except ValueError:
        i = len(ladder) - 1
    return ladder[max(i - 1, 0)]


def _at_max(rating: str, ladder: list[str]) -> bool:
    return rating == ladder[-1]


def _at_min(rating: str, ladder: list[str]) -> bool:
    return rating == ladder[0]


def t2k_flow(ctx):  # -> Flow (generator)
    data: T2KData = ctx.data  # type: ignore[assignment]
    draft = ctx.draft
    draft.method = "Life Path"
    if not isinstance(data, T2KData) or not data.has_careers:
        ctx.log("No T2K career data is indexed — cannot build a character.")
        draft.notes["Note"] = "The T2K library has no career data indexed yet."
        return

    nationality = yield from _nationality(ctx, draft)  # T2K step 1: languages + rank ladder
    yield from _attributes(ctx, data, draft)
    known: dict[str, str] = {}            # trained skill -> rating (untrained omitted)
    yield from _childhood(ctx, data, draft, known)
    yield from _career_terms(ctx, data, draft, known, nationality)
    yield from _at_war(ctx, draft, known)

    draft.skills = dict(sorted(known.items()))
    _finalize_derived(draft)

    draft.notes["Reminder"] = "Define a moral code and a 'buddy' (a key relationship) with your group."
    draft.notes.setdefault("Permanent rads", "0")
    draft.notes["Note"] = _FIDELITY_NOTE


def _attributes(ctx, data: T2KData, draft):
    for a in ATTRIBUTES:
        draft.attributes[a] = BASELINE_ATTR  # every attribute starts at C
    draft.derived["Coolness Under Fire"] = CUF_START
    roll = yield Step(
        "attr_increases", StepKind.ROLL,
        "Roll 2D3 for attribute increases",
        roll_spec=ATTR_INCREASE_DICE,
        detail="Attributes start at C (A best · D worst); each increase raises one one step.",
    )
    increases = roll.total or 0
    ctx.log(f"Rolled {increases} attribute increases on 2D3.")
    for n in range(increases):
        options = [
            Option(a, f"Raise {a}: {draft.attributes[a]} → {_step_up(draft.attributes[a], _ATTR_LADDER)}")
            for a in ATTRIBUTES if not _at_max(draft.attributes[a], _ATTR_LADDER)
        ]
        if not options:
            break
        pick = yield Step(
            f"attr_raise_{n}", StepKind.CHOICE,
            f"Attribute increase {n + 1} of {increases}: raise which?",
            options=options,
        )
        draft.attributes[pick.value] = _step_up(draft.attributes[pick.value], _ATTR_LADDER)


def _childhood(ctx, data: T2KData, draft, known: dict[str, str]):
    """The childhood D6 table: a class (upbringing) grants a starting skill trained
    to D. The class set + its skills come from the prose-parsed index data; skipped
    cleanly if that book has no childhood table indexed."""
    if not data.has_childhood:
        return
    pick = yield Step(
        "childhood", StepKind.CHOICE, "Childhood — what was your upbringing?",
        options=[Option(name, name, ", ".join(skills)) for name, skills in data.childhood],
        essential=True, detail="Your background trains one skill to D.",
    )
    cls = next((c for c in data.childhood if c[0] == pick.value), data.childhood[0])
    draft.notes["Childhood"] = cls[0]
    skill = yield Step(
        "childhood_skill", StepKind.CHOICE,
        f"{cls[0]} childhood — train one skill to D",
        options=[Option(s, s) for s in cls[1]],
    )
    _bump_skill(known, skill.value, ctx)

    # Childhood also grants one bonus specialty (rolled on a D6 over the background's
    # column). The book also allows choosing; if extraction dropped the rolled entry
    # (e.g. the D6=1 row), fall back to a choice among the background's specialties.
    specs = data.childhood_specialties.get(cls[0], {})
    if specs:
        roll = yield Step(
            "childhood_spec", StepKind.ROLL,
            f"{cls[0]} childhood — roll D6 for a bonus specialty",
            roll_spec="d6", detail="Your childhood grants one specialty.",
        )
        sp = specs.get(roll.total or 0)
        if not sp:
            choice = yield Step(
                "childhood_spec_pick", StepKind.CHOICE,
                f"{cls[0]} childhood — choose a bonus specialty",
                options=[Option(o, o) for o in sorted(set(specs.values()))],
            )
            sp = choice.value
        if sp and sp not in draft.specialties:
            draft.specialties.append(sp)
            ctx.log(f"Childhood specialty: {sp}")


def _bump_skill(known: dict[str, str], skill: str, ctx) -> None:
    known[skill] = _step_up(known.get(skill, "F"), _SKILL_LADDER)
    ctx.log(f"{skill} → {known[skill]}")


def _career_terms(ctx, data: T2KData, draft, known: dict[str, str], nationality: str):
    """Serve career terms until WWIII breaks out — a D8 'war' check that grows likelier
    each term (it triggers when the roll comes up under the number of terms served), or
    a safety cap is hit. Each term enters a career you *qualify* for, grants two distinct
    skill increases + an advancement roll (specialty + promotion), ages the character
    (D6), and — from the 2nd term — risks an age effect (a D8 check on the same
    rising threshold that drops one attribute a step)."""
    age = STARTING_AGE
    term = 0
    rank = _RankTrack(data, nationality)
    while term < TERM_HARD_CAP:
        term += 1
        # Hard-block: only offer careers the character qualifies for (advisory clauses
        # never block). If somehow none qualify, fall back to all so we can't dead-end.
        eligible = [c for c in data.careers
                    if data.eligibility(c, draft.attributes, draft.career_history)[0]]
        pool = eligible or data.careers
        options = [
            Option(c.name, c.name, (c.rank or "civilian") + (f" · {c.requirements}" if c.requirements else ""))
            for c in pool
        ]
        pick = yield Step(
            f"career_{term}", StepKind.CHOICE,
            f"Term {term}: choose a career", options=options, essential=True,
            detail="Only careers you currently qualify for are shown.",
        )
        career = data.career(pick.value)
        if career is None:
            break
        rank.enter(career, draft)

        bumps = SKILLS_PER_TERM
        raised: set[str] = set()   # two increases per term must be distinct skills
        # The first term must train Ranged Combat (if the career offers it).
        if term == 1 and any(s.lower() == RANGED_COMBAT.lower() for s in career.skills):
            _bump_skill(known, RANGED_COMBAT, ctx)
            raised.add(RANGED_COMBAT)
            bumps -= 1
        for b in range(bumps):
            choices = [s for s in career.skills
                       if s not in raised and not _at_max(known.get(s, "F"), _SKILL_LADDER)]
            if not choices:
                break
            sk = yield Step(
                f"skill_{term}_{b}", StepKind.CHOICE,
                f"Term {term}: raise a {career.name} skill",
                options=[Option(s, f"{s} ({known.get(s, 'F')} → {_step_up(known.get(s, 'F'), _SKILL_LADDER)})")
                         for s in choices],
            )
            _bump_skill(known, sk.value, ctx)
            raised.add(sk.value)

        spec_name = yield from _term_advancement(ctx, draft, career, known, term, rank)

        draft.gear = list(career.gear)  # most recent posting determines starting gear
        draft.career_history.append(career.name + (f" ({spec_name})" if spec_name else ""))

        age += (yield Step(
            f"age_{term}", StepKind.ROLL, f"Term {term}: years served",
            roll_spec=AGE_DICE, detail="Each term ages your character (D6).",
        )).total or 0
        draft.notes["Age"] = str(age)
        draft.notes["Terms served"] = str(term)

        if term == 1:
            continue  # the first term carries no age-effect or war risk

        yield from _age_effect(ctx, draft, term)

        war = yield Step(
            f"war_{term}", StepKind.ROLL, f"Term {term}: does WWIII break out?",
            roll_spec=WAR_DICE, detail=f"A D8 under {term} (your terms served) means war begins now.",
        )
        if (war.total or 0) < term:
            ctx.log(f"WWIII breaks out after {term} terms.")
            break


def _age_effect(ctx, draft, term: int):
    """From the 2nd term on, roll a D8: if it comes up under the number of terms
    served, age catches up and one attribute drops a step (player's choice; the bot
    picks in quick mode)."""
    roll = yield Step(
        f"age_effect_{term}", StepKind.ROLL, f"Term {term}: age effects",
        roll_spec=AGE_EFFECT_DICE, detail=f"A D8 under {term} (your terms served) costs an attribute step.",
    )
    if (roll.total or 0) >= term:
        return
    options = [
        Option(a, f"Lower {a}: {draft.attributes[a]} → {_step_down(draft.attributes[a], _ATTR_LADDER)}")
        for a in ATTRIBUTES if not _at_min(draft.attributes[a], _ATTR_LADDER)
    ]
    if not options:
        return
    drop = yield Step(
        f"age_drop_{term}", StepKind.CHOICE, "Age catches up — lower one attribute",
        options=options,
    )
    draft.attributes[drop.value] = _step_down(draft.attributes[drop.value], _ATTR_LADDER)
    ctx.log(f"Age effect: {drop.value} → {draft.attributes[drop.value]}")


def _term_advancement(ctx, draft, career, known: dict[str, str], term: int, rank: _RankTrack):
    """The term's advancement check (T2K's per-term skill roll). On a success you earn a
    specialty AND — if you hold a rank — a promotion (one step up your nationality's rank
    ladder, raising Coolness Under Fire a step); on a failure, neither. You only pick from
    specialties you don't already have (the book has you re-roll duplicates). The roll
    approximates a T2K skill roll with your best die (skill or attribute), succeeding on
    6+ — the exact skill→attribute pairing isn't in our data (see _FIDELITY_NOTE)."""
    skill_sides = [rating_to_sides(known[s]) for s in career.skills if s in known]
    attr_sides = [rating_to_sides(v) for v in draft.attributes.values()]
    die = max(skill_sides + attr_sides) if skill_sides else max(attr_sides, default=rating_to_sides("C"))
    roll = yield Step(
        f"advance_{term}", StepKind.ROLL,
        f"Term {term}: advancement check — roll your best {career.name} skill",
        roll_spec=f"d{die}",
        detail=f"{SUCCESS_THRESHOLD}+ earns a specialty"
               + (" and a promotion." if rank.has_rank else "."),
    )
    if (roll.total or 0) < SUCCESS_THRESHOLD:
        ctx.log(f"Advancement check failed (rolled {roll.total}) — no specialty or promotion.")
        return ""
    rank.promote(draft, ctx)
    available = [name for _roll, name in career.specialties if name not in draft.specialties]
    if not available:
        return ""
    sp = yield Step(
        f"spec_{term}", StepKind.CHOICE, f"Term {term}: specialty earned — pick one",
        options=[Option(name, name) for name in available],
    )
    if sp.value and sp.value not in draft.specialties:
        draft.specialties.append(sp.value)
    return sp.value


# --- Military rank ladder --------------------------------------------------
# Starting ranks in the career data are abbreviated; expand them to match the ladder.
_RANK_ALIASES = {
    "pfc": "private first class",
    "2nd lieutenant": "second lieutenant",
    "1st lieutenant": "first lieutenant",
}


def _norm_rank(name: str) -> str:
    s = " ".join((name or "").strip().lower().split())
    return _RANK_ALIASES.get(s, s)


def _rank_start_level(levels: list[str], rank_name: str) -> int:
    """The ladder index for a career's starting rank (exact match, then substring)."""
    want = _norm_rank(rank_name)
    for i, lv in enumerate(levels):
        if _norm_rank(lv) == want:
            return i
    for i, lv in enumerate(levels):
        if want and want in lv.lower():
            return i
    return 0


class _RankTrack:
    """Tracks the character's position on the nationality rank ladder through the term
    loop. Entering a military career lifts you to at least that career's starting rank;
    a promotion steps you up one level and raises CUF a step. Degrades gracefully to
    recording the starting rank only when the ranks table isn't indexed."""

    def __init__(self, data: T2KData, nationality: str):
        self._data = data
        self._nat = nationality
        self._levels = data.rank_levels()
        self.level = -1

    @property
    def has_rank(self) -> bool:
        return self.level >= 0

    def enter(self, career, draft) -> None:
        if not career.is_military:
            return
        if self._levels:
            self.level = max(self.level, _rank_start_level(self._levels, career.rank))
            draft.rank = self._data.rank_name(self._nat, self.level)
        elif not draft.rank:
            draft.rank = career.rank   # no ranks table indexed — record the start only

    def promote(self, draft, ctx) -> None:
        if not self.has_rank or not self._levels:
            return
        self.level = min(self.level + 1, len(self._levels) - 1)
        draft.rank = self._data.rank_name(self._nat, self.level)
        cuf = _step_up(draft.derived.get("Coolness Under Fire", CUF_START), _ATTR_LADDER)
        draft.derived["Coolness Under Fire"] = cuf
        ctx.log(f"Promoted to {draft.rank}; CUF → {cuf}.")


def _at_war(ctx, draft, known: dict[str, str]):
    yield Step(
        "atwar", StepKind.INFO, "The war breaks out — your final formative term.",
        detail="World War III has begun. Pick two skills you've already trained to sharpen.",
    )
    trained = sorted(known)
    if not trained:
        return
    raised: set[str] = set()   # sharpen two *distinct* skills
    for b in range(2):
        choices = [s for s in trained if s not in raised and not _at_max(known[s], _SKILL_LADDER)]
        if not choices:
            break
        sk = yield Step(
            f"atwar_skill_{b}", StepKind.CHOICE, "At War: raise a trained skill",
            options=[Option(s, f"{s} ({known[s]} → {_step_up(known[s], _SKILL_LADDER)})") for s in choices],
        )
        _bump_skill(known, sk.value, ctx)
        raised.add(sk.value)


def _finalize_derived(draft) -> None:
    def die(attr: str) -> int:
        return rating_to_sides(draft.attributes.get(attr, BASELINE_ATTR))

    draft.derived["Hit Capacity"] = str(math.ceil((die("STR") + die("AGL")) / 4))
    draft.derived["Stress Capacity"] = str(math.ceil((die("INT") + die("EMP")) / 4))


_NATIONS = [
    Option("us", "American (US)"),
    Option("soviet", "Soviet"),
    Option("polish", "Polish"),
    Option("swedish", "Swedish"),
    Option("other", "Other"),
]
# Warsaw-Pact origins start with some Russian alongside their native tongue + English.
_PACT = {"soviet", "polish"}


def _nationality(ctx, draft):
    """T2K character creation opens by choosing where you're from: it fixes your starting
    languages and your military rank ladder, so it leads the life path (in quick mode it's
    the first prompt) rather than trailing it as a finishing touch. Returns the chosen
    nationality key so the term loop can pick the matching rank ladder."""
    nat = yield Step(
        "nationality", StepKind.CHOICE, "Where is your character from?",
        options=_NATIONS, essential=True,
        detail="Sets starting languages and your rank ladder.",
    )
    draft.notes["Nationality"] = nat.detail
    langs = ["native language", "some English"]
    if nat.value in _PACT:
        langs.append("some Russian")
    draft.notes["Languages"] = ", ".join(langs)
    return nat.value


register(
    SystemChargen(
        name="Twilight 2000 (4E)",
        games=("twilight", "t2k", "2000"),
        build_flow=t2k_flow,
        build_data=build_t2k_data,
        extract_prose=extract_t2k_prose,
    )
)
