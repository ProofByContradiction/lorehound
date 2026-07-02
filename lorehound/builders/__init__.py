"""Equipment *builders* — mix-and-match configurators (the inverse of a stat card).

Where ``chargen`` generates a *character* by walking a life path, a builder assembles
a *piece of equipment* from indexed components within a budget: a Traveller powered
suit filling its slot allowance, a starship massing systems into a hull. Same spine as
``chargen`` — a system-agnostic engine drives a per-system flow that reads component
DATA from the live index (no copyrighted tables in the repo; see the stays-free rule).

The engine + step model are reused from ``chargen`` (they're system-agnostic); this
package adds the builder registry, per-system component data + flows, and rendering.
Each system module is imported here for its registration side effect.
"""

from . import armor, robot, ship  # noqa: F401 — register the Traveller builders
