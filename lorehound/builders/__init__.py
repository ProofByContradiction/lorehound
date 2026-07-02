"""Equipment *builders* — mix-and-match configurators (the inverse of a stat card).

Where ``chargen`` generates a *character* by walking a life path, a builder assembles
a *piece of equipment* from indexed components within a budget: a Traveller powered
suit filling its slot allowance, a starship massing systems into a hull. Same spine as
``chargen`` — a system-agnostic engine drives a per-system flow that reads component
DATA from the live index (no copyrighted tables in the repo; see the stays-free rule).

Only the data layer lives here so far (``armor``); the flow/engine/cog follow the
``chargen`` package's shape. Import each system module for its registration side effect
as those land.
"""
