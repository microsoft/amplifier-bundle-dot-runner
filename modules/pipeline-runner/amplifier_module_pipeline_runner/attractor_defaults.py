"""Attractor-layer policy defaults -- NOT engine-native.

DESIGN-worker-registry-core-split.md P3, gap-table row 22: ``DEFAULT_PROFILES``
(the provider -> ``attractor-agent-*`` routing map) is attractor *pattern*
policy, not engine *mechanism*, and its target is "engine-native defaults;
the attractor map moves to the wrapper." This module is that move: the
hard-coded map used to live at ``runner.py:51-55`` (the generic engine-driving
module every consumer -- including a future non-attractor caller -- imports);
it now lives here, a module whose name and docstring say plainly that it is
opinionated policy belonging to the legacy ``attractor`` personality, not the
engine.

Only the ``attractor`` CLI personality (``cli.py``'s ``build_parser(engine_native=
False)`` path) and ``runner.py``'s non-``engine_native`` code path consult this
module. The new ``dot-runner`` personality (``engine_native=True``) never
imports it -- see ``runner.py``'s ``run_pipeline``/``resume_pipeline``, which
default ``profiles`` to ``{}`` (never this map) when ``engine_native=True``.

Physically this still ships inside the same distribution as the engine CLI
-- console-script/package separation into attractor's own thin wrapper
distribution is an explicitly deferred, unratified Phase-3 design detail
(DESIGN doc Sec4 "Mechanics are deliberately under-specified"; Sec6 item 4).
This module is the seam that future split would cut along; for now it makes
the map's *ownership* legible even though its *distribution* hasn't moved.

Compat commitment (DESIGN doc Sec5.3): "The profiles map ... is not extended
and not repurposed." The values below are copied verbatim from the former
``runner.py`` site -- nothing here changes what any existing caller resolves.
"""

from __future__ import annotations

# Maps llm_provider node values -> child agent name, cribbed from
# agents/pipeline-runner.yaml / bundles/attractor-pipeline.yaml. This is the
# default provider->agent map used when the caller doesn't supply its own
# ``profiles`` (discover_profiles-from-graph is deferred to a later slice).
DEFAULT_PROFILES: dict[str, str] = {
    "anthropic": "attractor-agent-anthropic",
    "openai": "attractor-agent-openai",
    "gemini": "attractor-agent-gemini",
}
