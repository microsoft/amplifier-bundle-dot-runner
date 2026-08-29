"""Default-worker resolution: amplifier-agent-first, `direct`-fallback.

Maintainer policy (2026-08-29): amplifier-agent is the team's bet for new
dot-runner surfaces -- new surfaces default to it. When a run makes NO
explicit worker choice (no ``--worker``, no config, no ``--bundle``-declared
default), :func:`resolve` decides what the CLI does about it:

1. Probe availability of the in-repo adapter
   (``amplifier_module_loop_amplifier_agent`` -- ``modules/loop-amplifier-
   agent``, which hosts amplifier-agent's ``Engine`` per node via
   ``session.spawn``) AND its heavy peer dependency (``amplifier_agent_lib``,
   the thing that actually runs a turn). The probe
   (:func:`amplifier_agent_available`) is a cheap ``importlib.util.find_spec``
   check -- it locates a module without importing it, so it never pays
   amplifier-agent's heavy transitive import cost (fastapi/uvicorn/mcp/...)
   and never touches the network.

2. If BOTH resolve: synthesize a MINIMAL bundle
   (:func:`synthesize_default_agent_bundle_yaml`) declaring one agent entry
   backed by ``loop-amplifier-agent``, with a top-level
   ``session.orchestrator.config`` declaring ``worker: spawn`` (the
   registry's reserved sentinel -- see ``amplifier_module_loop_pipeline.
   backend._SPAWN_WORKER_SENTINEL``) and a ``profiles`` map routing every
   known LLM provider (``runner.PROVIDER_KEY_ENV``) to that one agent. Write
   it to a temp file (:func:`write_default_agent_bundle`) and hand its path
   back as this run's ``bundle=`` argument.

   This is the EXACT mechanism an explicit ``--bundle <ref>`` already uses --
   a local file path is a legitimate bundle reference
   (``amplifier_foundation.sources.file.FileSourceHandler``). No new runner
   internals are added: ``runner.run_pipeline``/``runner.resume_pipeline``
   see an ordinary ``bundle=`` string, load it via their own existing
   ``_load_named_bundle``, and read back its declared worker/profiles via
   their own existing ``_declared_worker_and_profiles`` -- both pre-existing,
   already-tested code paths, completely unchanged by this module.

3. If either is missing: print ONE loud stderr line naming the upgrade path
   (``UPGRADE_HINT``) and return the inputs unchanged. The pre-existing
   bare-engine fallback then resolves to ``direct`` exactly as it did before
   this module existed (``runner.run_pipeline``'s own
   ``elif bundle: ... else: resolved_worker = "direct"`` branch).

Explicit choices always win: :func:`resolve` is a no-op the moment the
caller already made ANY choice -- an explicit ``--worker``, or a bundle
reference from ``--bundle``/``DOT_RUNNER_BUNDLE`` (both arrive here already
merged by ``cli._resolve_bundle_ref`` before this module is consulted).
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

from . import runner

#: The adapter module this run wires as the hosted agent's orchestrator.
#: Lives in this same repo (modules/loop-amplifier-agent) -- see that
#: module's own README for the mount contract this bundle entry relies on.
_ADAPTER_MODULE = "amplifier_module_loop_amplifier_agent"

#: amplifier-agent's own heavy peer library -- the thing that actually runs
#: an agent turn. The adapter's top-level import surface deliberately never
#: imports this (see modules/loop-amplifier-agent/README.md, "Python version
#: note"), so it must be probed independently: the adapter package can be
#: installed and importable while amplifier_agent_lib itself is absent
#: (e.g. the `[agent]` extra was never installed) or unimportable (e.g.
#: Python <3.12, amplifier_agent_lib's own floor).
_AGENT_LIB_MODULE = "amplifier_agent_lib"

#: Name of the single synthesized agent entry every known provider routes to.
DEFAULT_AGENT_NAME = "dot-runner-default-agent"

#: The adapter's own published install location -- mirrors the git ref
#: modules/loop-amplifier-agent/README.md and behaviors/dot-runner-
#: amplifier-agent.yaml already document for wiring this same orchestrator
#: into a consuming bundle's `agents:` block.
_ADAPTER_SOURCE = (
    "git+https://github.com/microsoft/amplifier-bundle-dot-runner@main"
    "#subdirectory=modules/loop-amplifier-agent"
)

#: Upgrade hint printed on the ONE stderr fallback line when amplifier-agent
#: is not installed.
#:
#: Deliberately does NOT tell the reader to run
#: ``uv tool install "amplifier-dot-runner[agent]"`` as a single command:
#: that single-solve install can fail today with `uv` reporting "conflicting
#: URLs for package amplifier-foundation" (a real, disclosed upstream/
#: cross-repo dependency-declaration mismatch -- see README's "The [agent]
#: extra" section for the full explanation). Teaching a command known to
#: fail is worse than teaching nothing, so this notice points at the
#: two-step install the README proves works today instead.
UPGRADE_HINT = (
    'see this repo\'s README, "The [agent] extra" section, for the '
    "two-step install that enables it today (a single "
    '`uv tool install "amplifier-dot-runner[agent]"` can hit a known uv '
    "dependency-resolution collision)"
)

#: The SAME generic session.context module runner._bare_base_bundle() mounts
#: for every other bare (no --bundle) run -- shared engine infrastructure,
#: not pattern-repo policy (see runner.py's own ``_CONTEXT_SIMPLE_GIT``
#: docstring). This bundle needs its own copy because it is composed as the
#: run's BASE bundle (replacing ``_bare_base_bundle()`` outright), not
#: merged alongside it.
_CONTEXT_SIMPLE_GIT = (
    "git+https://github.com/microsoft/amplifier-module-context-simple@main"
)


def amplifier_agent_available() -> bool:
    """Cheap, no-network availability probe.

    ``importlib.util.find_spec`` locates a module (walks ``sys.path`` /
    reads installed-package metadata) without importing it -- no heavy
    transitive dependency tree is pulled in, and nothing reaches the
    network. Both the adapter AND ``amplifier_agent_lib`` must resolve: the
    adapter alone (e.g. a stale partial install) cannot host a real turn
    without its peer library.

    A malformed/oddly-named spec lookup raises ``ValueError`` in some
    Python versions (e.g. for a name containing invalid characters) rather
    than returning ``None`` -- both known module names here are plain
    dotted identifiers, but the probe stays tolerant of that anyway so a
    weird environment degrades to "unavailable", never to an uncaught
    crash that would take the whole CLI down before a single node runs.
    """
    try:
        adapter_spec = importlib.util.find_spec(_ADAPTER_MODULE)
    except (ImportError, ValueError, ModuleNotFoundError):
        adapter_spec = None
    if adapter_spec is None:
        return False

    try:
        lib_spec = importlib.util.find_spec(_AGENT_LIB_MODULE)
    except (ImportError, ValueError, ModuleNotFoundError):
        lib_spec = None
    return lib_spec is not None


def synthesize_default_agent_bundle_yaml() -> str:
    """Return the minimal bundle YAML text wiring amplifier-agent as the
    default spawn orchestrator.

    Mirrors ``behaviors/dot-runner-amplifier-agent.yaml``'s established
    agent-entry shape (same orchestrator ``module``/``source``) -- the only
    addition is a top-level ``session.orchestrator.config`` declaring
    ``worker: spawn`` and a ``profiles`` map, so ``runner.
    _declared_worker_and_profiles`` (an EXISTING, already-tested reader --
    see runner.py) picks it up as this run's effective default the exact
    same way it would for any explicitly loaded bundle. Deliberately
    minimal: no ``module:`` on the top-level orchestrator (the runtime
    overlay ``_build_prepared`` composes on top always supplies one), no
    extra config keys the adapter doesn't read.
    """
    providers = sorted(runner.PROVIDER_KEY_ENV)
    profile_lines = "\n".join(
        f"        {provider}: {DEFAULT_AGENT_NAME}" for provider in providers
    )
    return f"""\
bundle:
  name: dot-runner-default-agent-bundle
  version: 0.1.0
  description: >
    Auto-synthesized by dot-runner's default-worker resolution
    (amplifier_module_pipeline_runner.default_worker) when a run makes no
    explicit --worker/--bundle/config choice. Wires microsoft/amplifier-agent
    (via the in-repo loop-amplifier-agent adapter) as the run's spawn
    orchestrator -- the maintainer bet for new dot-runner surfaces. See the
    root README's "Default worker" section.
session:
  context:
    # AmplifierSession construction requires SOME session.context to be
    # present. This bundle is composed as the run's BASE bundle (it REPLACES
    # runner._bare_base_bundle(), it is not merged alongside it -- see
    # runner.run_pipeline's `base_bundle` handling), so it must supply its
    # own -- the exact same generic, pattern-repo-agnostic module
    # runner._bare_base_bundle() already mounts for every other bare run.
    module: context-simple
    source: {_CONTEXT_SIMPLE_GIT}
  orchestrator:
    config:
      worker: spawn
      profiles:
{profile_lines}
agents:
  {DEFAULT_AGENT_NAME}:
    description: >
      Default pipeline-node worker -- microsoft/amplifier-agent's own Engine,
      hosted via the loop-amplifier-agent adapter (modules/loop-amplifier-agent).
    session:
      orchestrator:
        module: loop-amplifier-agent
        source: {_ADAPTER_SOURCE}
        config:
          llm_provider: anthropic
"""


def write_default_agent_bundle() -> Path:
    """Write the synthesized bundle YAML to a fresh temp file, return its path.

    A dedicated temp directory (mirrors ``cli.py``'s own
    ``tempfile.mkdtemp(prefix="dot-runner-run-")`` pattern for the default
    ``--logs-root``) -- independent of this run's own ``--logs-root``/
    ``--cwd`` so writing it never races their creation, and safe to call
    before either is resolved.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="dot-runner-default-agent-"))
    bundle_path = tmp_dir / "bundle.yaml"
    bundle_path.write_text(synthesize_default_agent_bundle_yaml(), encoding="utf-8")
    return bundle_path


def resolve(
    *, worker: str | None, bundle: str | None, prog: str = "dot-runner"
) -> tuple[str | None, str | None]:
    """Resolve this run's effective ``(worker, bundle)`` pair.

    Explicit choices always win -- a no-op the moment the caller already
    made ANY choice (an explicit ``--worker``, or a bundle reference from
    ``--bundle``/``DOT_RUNNER_BUNDLE``, already merged into ``bundle`` by
    ``cli._resolve_bundle_ref`` before this is called): both are returned
    unchanged.

    Only when BOTH are absent -- the true "no explicit choice" case this
    module exists for -- does it attempt amplifier-agent:

    * available -> synthesize + wire the minimal bundle above and return it
      as ``bundle`` (``worker`` stays ``None``, so ``run_pipeline``'s own
      ``elif bundle: resolved_worker = declared_worker`` branch reads
      ``"spawn"`` back from the synthesized bundle's own declared config --
      precisely mirroring what an explicit ``--bundle`` would produce, not
      a new selection path).
    * unavailable -> print ONE loud stderr line naming the upgrade path and
      return the inputs unchanged, letting the pre-existing bare-engine
      fallback chain resolve to ``direct`` exactly as it did before.
    """
    if worker is not None or bundle is not None:
        return worker, bundle

    if amplifier_agent_available():
        return worker, str(write_default_agent_bundle())

    print(
        f"{prog}: no --worker/--bundle given and amplifier-agent is not "
        f"installed -- falling back to worker=direct. Install the agent "
        f"extra for the default amplifier-agent worker: {UPGRADE_HINT}",
        file=sys.stderr,
    )
    return worker, bundle
