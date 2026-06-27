"""Twilight 2000 chargen data — a typed, snapshot view over the rules index.

The flow needs each career's requirements / starting rank / skill list / specialty
table / starting gear. Those are already recovered by the generic career-card
detector (:mod:`lorehound.careers`) as :class:`Career` sections; here we adapt them
into a flat :class:`T2KCareer` so the flow reads fields instead of string-matching
section labels, and snapshot the whole set ONCE at session start (see
:class:`T2KData`) so an in-flight character is unaffected by a mid-session re-index.

Requirement *text* (e.g. ``"STR and AGL B+, INT C+, at least one term in Combat
Arms"``) is parsed here into structured predicates the flow can enforce, and the
military rank ladder is read from ``chargen_aux`` (sourced from the index, not
embedded). No rulebook tables are embedded — everything is read from the live index.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

# Attribute ladder for threshold comparisons (D worst … A best).
_ATTR_RANK = {"D": 0, "C": 1, "B": 2, "A": 3}
_ATTRS = ("STR", "AGL", "INT", "EMP")
# The T2K "Education" functional group (the academic careers) isn't recoverable from
# the per-career data, so name its members explicitly — kept tiny and obvious.
_EDUCATION = {"liberal arts", "sciences"}
_TERM_COUNTS = {
    "no": 0, "a": 1, "one": 1, "at least one": 1, "one or more": 1, "two": 2, "three": 3,
}
_BLANK_REQ = {"", "none", "–", "-", "—", "n/a"}


@dataclass
class ReqClause:
    """One parsed requirement clause. ``check(attrs, history_careers)`` is the test;
    ``parseable`` is False for clauses we couldn't read (those never hard-block — they
    fall back to advisory, per the enforcement design)."""

    text: str
    parseable: bool
    check: Callable[[dict[str, str], list], bool] = field(default=lambda a, h: True)


def _attr_level(attrs: dict[str, str], name: str) -> int:
    return _ATTR_RANK.get(attrs.get(name, "C"), 1)


def _count_terms(history: list, target: str) -> int:
    """How many served terms match ``target`` over ``(name, career)`` history pairs — a
    career name (matched by name, so it works even if that career isn't in the snapshot),
    ``military`` (any ranked career), or ``Education`` / ``Education (Sciences)`` (the
    academic group)."""
    t = re.sub(r"^(the|an?)\s+", "", target.strip().lower()).strip()
    edu = re.match(r"^education(?:\s*\((.+)\))?$", t)

    def matches(name: str, career) -> bool:
        name = name.lower()
        if t == "military":
            return bool(career and career.is_military)
        if edu:
            sub = (edu.group(1) or "").strip().lower()
            if sub and sub != "any":
                return name == sub
            return name in _EDUCATION
        return name == t or name.startswith(t)

    return sum(1 for name, career in history if matches(name, career))


def _parse_clause(raw: str) -> ReqClause | None:
    """Parse a single requirement clause into a :class:`ReqClause`."""
    s = raw.strip()
    if not s:
        return None
    low = s.lower()

    # "no D attribute" — no attribute may sit at the named level.
    m = re.match(r"^no\s+([abcd])\s+attribute", low)
    if m:
        bad = m.group(1).upper()
        return ReqClause(s, True, lambda a, h, bad=bad: all(a.get(x) != bad for x in _ATTRS))

    # "EMP C or D" — the attribute must be one of the listed levels.
    m = re.match(r"^([a-z]{3})\s+([abcd])\s+or\s+([abcd])$", low)
    if m and m.group(1).upper() in _ATTRS:
        attr = m.group(1).upper()
        allowed = {m.group(2).upper(), m.group(3).upper()}
        return ReqClause(s, True, lambda a, h, attr=attr, ok=allowed: a.get(attr, "C") in ok)

    # "STR or AGL B+", "INT C+", "STR and AGL B+" — threshold, AND/OR across attributes.
    m = re.match(r"^([a-z]{3}(?:\s+(?:and|or)\s+[a-z]{3})*)\s+([abcd])\+$", low)
    if m:
        names = [w.upper() for w in re.findall(r"[a-z]{3}", m.group(1)) if w.upper() in _ATTRS]
        if names:
            need = _ATTR_RANK[m.group(2).upper()]
            any_of = " or " in m.group(1)

            def check(a, h, names=names, need=need, any_of=any_of):
                hits = [_attr_level(a, n) >= need for n in names]
                return any(hits) if any_of else all(hits)

            return ReqClause(s, True, check)

    # "at least one term in Combat Arms", "no terms in prison", "Two terms in
    # Education (Sciences)", "one or more terms as an Agent".
    m = re.match(r"^(no|at least one|one or more|two|three|one|a)\s+terms?\s+(?:in|as)\s+(.+)$", low)
    if m:
        count = _TERM_COUNTS.get(m.group(1), 1)
        target = m.group(2).strip()
        if count == 0:
            return ReqClause(s, True, lambda a, h, t=target: _count_terms(h, t) == 0)
        return ReqClause(s, True, lambda a, h, t=target, n=count: _count_terms(h, t) >= n)

    # Anything else (e.g. Officer's "requirements for the functional area") — keep as an
    # advisory clause that never blocks.
    return ReqClause(s, False)


def parse_requirements(text: str) -> list[ReqClause]:
    """Parse a career's freeform requirement text into enforceable clauses; ``[]`` when
    there are no requirements (``None`` / ``–``)."""
    text = (text or "").strip()
    if text.lower() in _BLANK_REQ:
        return []
    clauses: list[ReqClause] = []
    for part in text.split(","):
        clause = _parse_clause(part)
        if clause is not None:
            clauses.append(clause)
    return clauses


@dataclass
class T2KCareer:
    """One T2K life-path career/branch, flattened from a detected career card."""

    name: str
    requirements: str = ""                                   # freeform, shown as guidance
    req_clauses: list[ReqClause] = field(default_factory=list)  # parsed, enforceable
    rank: str = ""                                           # starting rank ("" if civilian)
    skills: list[str] = field(default_factory=list)          # career skills
    specialties: list[tuple[str, str]] = field(default_factory=list)  # (roll, name)
    gear: list[str] = field(default_factory=list)            # starting-gear items
    source: str = ""
    locator: str = ""

    def __post_init__(self) -> None:
        # Derive enforceable clauses from the requirement text unless given explicitly,
        # so constructing a career from just its text (tests, future systems) still gates.
        if self.requirements and not self.req_clauses:
            self.req_clauses = parse_requirements(self.requirements)

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
    req_text = req.text if req else ""
    return T2KCareer(
        name=career.name,
        requirements=req_text,
        req_clauses=parse_requirements(req_text),
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
    # Childhood D6 classes: (class_name, [skill, skill, skill]); empty if not indexed.
    childhood: list[tuple[str, list[str]]] = field(default_factory=list)
    # Childhood bonus specialties: {class_name: {d6: specialty}}; the roll-of-1 entry may
    # be absent (the extraction drops that grid row), so the flow falls back to a choice.
    childhood_specialties: dict = field(default_factory=dict)
    # Military rank ladder from the index: {"columns": [...], "rows": [[r0,r1,r2,r3], …]}
    # ascending by level; {} if the ranks table wasn't indexed (promotions degrade to
    # recording the starting rank only).
    ranks: dict = field(default_factory=dict)

    def career(self, name: str) -> T2KCareer | None:
        nl = name.strip().lower()
        return next((c for c in self.careers if c.name.lower() == nl), None)

    @property
    def has_careers(self) -> bool:
        return bool(self.careers)

    @property
    def has_childhood(self) -> bool:
        return bool(self.childhood)

    # --- requirement enforcement ------------------------------------------

    def eligibility(
        self, career: T2KCareer, attrs: dict[str, str], history_names: list[str]
    ) -> tuple[bool, list[str]]:
        """``(eligible, unmet_reasons)`` for ``career`` given the draft's attributes and
        career history. Only *parseable* clauses can make a career ineligible; clauses
        we couldn't parse are advisory and never block."""
        hist = []
        for entry in history_names:
            name = _strip_specialty(entry)
            hist.append((name, self.career(name)))
        unmet = [c.text for c in career.req_clauses if c.parseable and not c.check(attrs, hist)]
        return (not unmet), unmet

    # --- rank ladder ------------------------------------------------------

    def rank_levels(self) -> list[str]:
        """The rank ladder spine (US column / level labels), ascending; [] if none."""
        return [r[0] for r in self.ranks.get("rows", [])]

    def rank_name(self, nationality: str, level: int) -> str:
        """The localized rank name at ``level`` for ``nationality`` (falls back to the
        spine label where that nation has no equivalent)."""
        rows = self.ranks.get("rows", [])
        if not rows:
            return ""
        cols = self.ranks.get("columns", [])
        level = max(0, min(level, len(rows) - 1))
        col = cols.index(nationality) if nationality in cols else 0
        name = rows[level][col].strip()
        return name if name and name not in {"–", "-", "—"} else rows[level][0]


def _strip_specialty(history_entry: str) -> str:
    """``"Combat Arms (Rifleman)" -> "Combat Arms"`` so a history entry matches a career
    name."""
    return history_entry.split(" (", 1)[0].strip()


def build_t2k_data(rules, game: str) -> T2KData:
    """Snapshot the indexed careers for ``game`` into a :class:`T2KData`. Reads the
    structured-career index built at index time; flattens each into a T2KCareer.
    Careers with no usable skills/specialties are dropped (low-quality detections).
    Childhood and the rank ladder come from the prose-parsed ``chargen_aux``."""
    detected = rules.careers.get(game, {})
    careers: list[T2KCareer] = []
    for career in detected.values():
        tc = t2k_career_from(career)
        if tc.skills or tc.specialties:   # needs at least something to build on
            careers.append(tc)
    careers.sort(key=lambda c: (not c.is_military, c.name))  # military first, then A→Z
    aux = getattr(rules, "chargen_aux", {}).get(game, {})
    childhood = [(c, list(skills)) for c, skills in aux.get("childhood", [])]
    return T2KData(
        game=game,
        careers=careers,
        childhood=childhood,
        childhood_specialties=aux.get("childhood_specialties", {}),
        ranks=aux.get("ranks", {}),
    )
