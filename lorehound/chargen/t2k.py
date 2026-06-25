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

from ..twilight import rating_to_sides
from .data import T2KData, build_t2k_data
from .model import Option, Step, StepKind
from .registry import SystemChargen, register

ATTRIBUTES = ("STR", "AGL", "INT", "EMP")
_ATTR_LADDER = ["D", "C", "B", "A"]    # ascending; A is best
_SKILL_LADDER = ["F", "D", "C", "B", "A"]  # F = untrained

# --- Allocation amounts (APPROXIMATE — see module docstring) ---------------
BASELINE_ATTR = "C"          # "average Jane or Joe" per the core book
BASE_ATTR_INCREASES = 2      # one-step raises from the C baseline
DROP_BONUS_INCREASES = 2     # extra raises gained by dropping one attribute to D
SKILLS_PER_TERM = 2          # one-step skill raises granted each term
MAX_TERMS = 4                # career terms before the mandatory "At War" term
RANGED_COMBAT = "Ranged Combat"  # first term must train this (T2K life-path rule)

_APPROX_NOTE = (
    "Attribute/skill allocation uses a standard spread (C baseline, "
    f"{BASE_ATTR_INCREASES} increases; drop one to D for +{DROP_BONUS_INCREASES}). "
    "Confirm against your table for strict rules-as-written."
)


def _step_up(rating: str, ladder: list[str]) -> str:
    """One step up the ladder toward A, capped at A."""
    try:
        i = ladder.index(rating)
    except ValueError:
        i = 0
    return ladder[min(i + 1, len(ladder) - 1)]


def _at_max(rating: str, ladder: list[str]) -> bool:
    return rating == ladder[-1]


def t2k_flow(ctx):  # -> Flow (generator)
    data: T2KData = ctx.data  # type: ignore[assignment]
    draft = ctx.draft
    draft.method = "Life Path"
    if not isinstance(data, T2KData) or not data.has_careers:
        ctx.log("No T2K career data is indexed — cannot build a character.")
        draft.notes["Note"] = "The T2K library has no career data indexed yet."
        return

    yield from _attributes(ctx, data, draft)
    known: dict[str, str] = {}            # trained skill -> rating (untrained omitted)
    yield from _career_terms(ctx, data, draft, known)
    yield from _at_war(ctx, draft, known)

    draft.skills = dict(sorted(known.items()))
    _finalize_derived(draft)
    yield from _finishing_touches(ctx, draft)

    draft.notes["Reminder"] = "Define a moral code and a 'buddy' (a key relationship) with your group."
    draft.notes.setdefault("Permanent rads", "0")
    draft.notes["Note"] = _APPROX_NOTE


def _attributes(ctx, data: T2KData, draft):
    for a in ATTRIBUTES:
        draft.attributes[a] = BASELINE_ATTR
    drop = yield Step(
        "attr_drop", StepKind.CHOICE,
        "Attributes start at C. Drop one to D for extra increases?",
        options=[Option("none", "Keep all at C")]
        + [Option(a, f"Drop {a} to D") for a in ATTRIBUTES],
        detail="Ratings run A (best) · B · C · D (worst).",
    )
    increases = BASE_ATTR_INCREASES
    if drop.value in ATTRIBUTES:
        draft.attributes[drop.value] = "D"
        increases += DROP_BONUS_INCREASES
        ctx.log(f"Dropped {drop.value} to D for +{DROP_BONUS_INCREASES} attribute increases.")
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


def _bump_skill(known: dict[str, str], skill: str, ctx) -> None:
    known[skill] = _step_up(known.get(skill, "F"), _SKILL_LADDER)
    ctx.log(f"{skill} → {known[skill]}")


def _career_terms(ctx, data: T2KData, draft, known: dict[str, str]):
    for term in range(1, MAX_TERMS + 1):
        options = [
            Option(c.name, c.name, (c.rank or "civilian") + (f" · {c.requirements}" if c.requirements else ""))
            for c in data.careers
        ]
        pick = yield Step(
            f"career_{term}", StepKind.CHOICE,
            f"Term {term}: choose a career", options=options, essential=True,
        )
        career = data.career(pick.value)
        if career is None:
            break
        if career.is_military and not draft.rank:
            draft.rank = career.rank

        bumps = SKILLS_PER_TERM
        # The first term must train Ranged Combat (if the career offers it).
        if term == 1 and any(s.lower() == RANGED_COMBAT.lower() for s in career.skills):
            _bump_skill(known, RANGED_COMBAT, ctx)
            bumps -= 1
        for b in range(bumps):
            choices = [s for s in career.skills if not _at_max(known.get(s, "F"), _SKILL_LADDER)]
            if not choices:
                break
            sk = yield Step(
                f"skill_{term}_{b}", StepKind.CHOICE,
                f"Term {term}: raise a {career.name} skill",
                options=[Option(s, f"{s} ({known.get(s, 'F')} → {_step_up(known.get(s, 'F'), _SKILL_LADDER)})")
                         for s in choices],
            )
            _bump_skill(known, sk.value, ctx)

        spec_name = ""
        if career.specialties:
            sp = yield Step(
                f"spec_{term}", StepKind.CHOICE,
                f"Term {term}: pick a {career.name} specialty",
                options=[Option(name, name) for _roll, name in career.specialties],
            )
            spec_name = sp.value
            if spec_name and spec_name not in draft.specialties:
                draft.specialties.append(spec_name)

        draft.gear = list(career.gear)  # most recent posting determines starting gear
        draft.career_history.append(career.name + (f" ({spec_name})" if spec_name else ""))

        if term < MAX_TERMS:
            cont = yield Step(
                f"more_{term}", StepKind.CHOICE, "Serve another term?",
                options=[Option("yes", "Yes — serve again"), Option("no", "No — the war is here")],
                essential=True,
            )
            if cont.value == "no":
                break


def _at_war(ctx, draft, known: dict[str, str]):
    yield Step(
        "atwar", StepKind.INFO, "The war breaks out — your final formative term.",
        detail="World War III has begun. Pick two skills you've already trained to sharpen.",
    )
    trained = sorted(known)
    if not trained:
        return
    for b in range(2):
        choices = [s for s in trained if not _at_max(known[s], _SKILL_LADDER)]
        if not choices:
            break
        sk = yield Step(
            f"atwar_skill_{b}", StepKind.CHOICE, "At War: raise a trained skill",
            options=[Option(s, f"{s} ({known[s]} → {_step_up(known[s], _SKILL_LADDER)})") for s in choices],
        )
        _bump_skill(known, sk.value, ctx)


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


def _finishing_touches(ctx, draft):
    nat = yield Step(
        "nationality", StepKind.CHOICE, "Where is your character from?",
        options=_NATIONS, essential=True,
        detail="Sets starting languages and colours your gear.",
    )
    draft.notes["Nationality"] = nat.detail
    langs = ["native language", "some English"]
    if nat.value in _PACT:
        langs.append("some Russian")
    draft.notes["Languages"] = ", ".join(langs)


register(
    SystemChargen(
        name="Twilight 2000 (4E)",
        games=("twilight", "t2k", "2000"),
        build_flow=t2k_flow,
        build_data=build_t2k_data,
    )
)
