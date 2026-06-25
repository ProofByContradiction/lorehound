"""Twilight 2000 chargen data — a typed, snapshot view over the rules index.

The flow needs each career's requirements / starting rank / skill list / specialty
table / starting gear. Those are already recovered by the generic career-card
detector (:mod:`lorehound.careers`) as :class:`Career` sections; here we adapt them
into a flat :class:`T2KCareer` so the flow reads fields instead of string-matching
section labels, and snapshot the whole set ONCE at session start (see
:class:`T2KData`) so an in-flight character is unaffected by a mid-session re-index.

No rulebook tables are embedded here — everything is read from the live index.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class T2KCareer:
    """One T2K life-path career/branch, flattened from a detected career card."""

    name: str
    requirements: str = ""                                   # freeform, shown as guidance
    rank: str = ""                                           # starting rank ("" if civilian)
    skills: list[str] = field(default_factory=list)          # career skills
    specialties: list[tuple[str, str]] = field(default_factory=list)  # (roll, name)
    gear: list[str] = field(default_factory=list)            # starting-gear items
    source: str = ""
    locator: str = ""

    @property
    def is_military(self) -> bool:
        """Military branches carry a starting rank; civilian careers don't."""
        return bool(self.rank)


def _split_skills(text: str) -> list[str]:
    return [s.strip() for s in text.replace(";", ",").split(",") if s.strip()]


def _split_gear(text: str) -> list[str]:
    # Starting gear is a ✓-bulleted list ("✓ Assault rifle ✓ D6 reloads …"); fall
    # back to comma-splitting if a card used plain prose instead.
    raw = text.split("✓") if "✓" in text else text.split(",")
    return [g.strip(" ,") for g in raw if g.strip(" ,")]


def _section(career, *needles: str):
    """The first career section whose label contains any needle (case-insensitive)."""
    for s in career.sections:
        low = s.label.lower()
        if any(n in low for n in needles):
            return s
    return None


def t2k_career_from(career) -> T2KCareer:
    """Flatten a detected :class:`~lorehound.careers.Career` into a :class:`T2KCareer`."""
    req = _section(career, "requirement")
    rank = _section(career, "rank")
    skills = _section(career, "skill")
    gear = _section(career, "gear", "equipment")
    spec = next((s for s in career.sections if s.rows and "special" in s.label.lower()), None)
    specialties: list[tuple[str, str]] = []
    if spec:
        for r in spec.rows[1:]:  # skip the header row
            if len(r) >= 2 and r[0].strip() and r[1].strip():
                specialties.append((r[0].strip(), r[1].strip()))
    return T2KCareer(
        name=career.name,
        requirements=(req.text if req else ""),
        rank=(rank.text if rank else ""),
        skills=_split_skills(skills.text) if skills else [],
        specialties=specialties,
        gear=_split_gear(gear.text) if gear else [],
        source=career.source,
        locator=career.locator,
    )


@dataclass
class T2KData:
    """A consistent snapshot of the T2K chargen data for one session."""

    game: str
    careers: list[T2KCareer] = field(default_factory=list)

    def career(self, name: str) -> T2KCareer | None:
        nl = name.strip().lower()
        return next((c for c in self.careers if c.name.lower() == nl), None)

    @property
    def has_careers(self) -> bool:
        return bool(self.careers)


def build_t2k_data(rules, game: str) -> T2KData:
    """Snapshot the indexed careers for ``game`` into a :class:`T2KData`. Reads the
    structured-career index built at index time; flattens each into a T2KCareer.
    Careers with no usable skills/specialties are dropped (low-quality detections)."""
    detected = rules.careers.get(game, {})
    careers: list[T2KCareer] = []
    for career in detected.values():
        tc = t2k_career_from(career)
        if tc.skills or tc.specialties:   # needs at least something to build on
            careers.append(tc)
    careers.sort(key=lambda c: (not c.is_military, c.name))  # military first, then A→Z
    return T2KData(game=game, careers=careers)
