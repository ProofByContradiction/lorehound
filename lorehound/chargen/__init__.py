"""Interactive, system-agnostic character generation.

A character generator is split in two:

* a system-agnostic **engine** (:mod:`lorehound.chargen.engine`) that walks a
  per-system flow and applies one of two traversal policies — *quick* (auto-roll,
  prompt only for genuine decisions) or *faithful* (surface every roll/choice);
* a per-system **flow** registered in :mod:`lorehound.chargen.registry`, expressed
  as a generator that yields :class:`~lorehound.chargen.model.Step` objects and
  receives the resolved answers back. Twilight 2000 is the first
  (:mod:`lorehound.chargen.t2k`).

Flows read game data from the live rules index at runtime (see
:mod:`lorehound.chargen.data`); only the generic algorithm and non-copyrightable
mechanics live in code, so no rulebook tables are committed to the repo.
"""

from __future__ import annotations
