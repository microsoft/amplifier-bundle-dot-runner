"""Named-worker resolution: ``direct`` | ``loop-agent`` | ``amplifier-agent``.

Maintainer policy (2026-08-29/30, WAVE 5 repair): worker NAMES are the whole
user concept (ruling: "bundles are under the hood -- never exposed to
runner users"). ``--worker direct|loop-agent|amplifier-agent`` (or a node's
own ``worker=`` attribute, EXTENSIONS.md Sec40) is the complete user-facing
story; ``--bundle``/``DOT_RUNNER_BUNDLE`` are REMOVED from the CLI surface
entirely (no flag, no env var, no help text, no README mention) -- see
``cli.py``'s module docstring. Any bundle machinery a named worker needs
under the hood stays exactly that: internal, private, never bundle
vocabulary in a signature, an error message, or a doc.

amplifier-agent is the team's bet for new dot-runner surfaces: when a run
makes NO explicit worker choice (no ``--worker``, no node ``worker=``),
:func:`resolve` decides what the CLI does about it:

1. Probe availability of the in-repo adapter
   (``amplifier_module_loop_amplifier_agent`` -- ``modules/loop-amplifier-
   agent``, which hosts amplifier-agent's ``Engine`` per node via
   ``session.spawn``) AND its heavy peer dependency (``amplifier_agent_lib``,
   the thing that actually runs a turn). The probe
   (:func:`amplifier_agent_available`) is a cheap ``importlib.util.find_spec``
   check -- it locates a module without importing it, so it never pays
   amplifier-agent's heavy transitive import cost (its own web-framework/
   ASGI-server stack, MCP client libs, etc. -- see amplifier_agent_lib's own
   pyproject.toml for the full list) and never touches the network.

2. If BOTH resolve: synthesize a MINIMAL bundle
   (:func:`_synthesize_agent_bundle_yaml`) declaring one agent entry
   backed by ``loop-amplifier-agent`` (the same synthesis this module uses
   for an EXPLICIT ``--worker loop-agent``/``--worker amplifier-agent``
   choice too -- see :func:`resolve` -- just parametrized by which adapter
   module to wire), with a top-level
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

#: Registered worker NAMES this module knows how to wire under the hood --
#: the whole user-facing vocabulary (``--worker <name>`` / node ``worker=``).
#: Each maps to an adapter module living in this same repo, wired internally
#: via a synthesized single-agent bundle (:func:`_synthesize_agent_bundle_yaml`)
#: -- bundle vocabulary never reaches the CLI surface (help/errors/docs).
LOOP_AGENT_NAME = "loop-agent"
AMPLIFIER_AGENT_NAME = "amplifier-agent"

#: name -> (adapter module, its probe module for availability, its published
#: git-ref source). ``probe_module`` is what :func:`_worker_available` runs
#: ``importlib.util.find_spec`` against -- for amplifier-agent this is its
#: heavy peer library (never imported at this module's top level, see
#: modules/loop-amplifier-agent/README.md "Python version note"); loop-agent
#: has no comparable heavy peer, so its own adapter module doubles as the probe.
_ADAPTER_REGISTRY: dict[str, tuple[str, str, str]] = {
    LOOP_AGENT_NAME: (
        "amplifier_module_loop_agent",
        "amplifier_module_loop_agent",
        (
            "git+https://github.com/microsoft/amplifier-bundle-dot-runner@main"
            "#subdirectory=modules/loop-agent"
        ),
    ),
    AMPLIFIER_AGENT_NAME: (
        "amplifier_module_loop_amplifier_agent",
        "amplifier_agent_lib",
        (
            "git+https://github.com/microsoft/amplifier-bundle-dot-runner@main"
            "#subdirectory=modules/loop-amplifier-agent"
        ),
    ),
}

#: Name of the single synthesized agent entry every known provider routes to.
DEFAULT_AGENT_NAME = "dot-runner-default-agent"

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


def _worker_available(name: str) -> bool:
    """Cheap, no-network availability probe for a named worker.

    ``importlib.util.find_spec`` locates a module (walks ``sys.path`` /
    reads installed-package metadata) without importing it -- no heavy
    transitive dependency tree is pulled in, and nothing reaches the
    network. Both the adapter AND its probe module (its own heavy peer
    library, when it has one) must resolve.

    A malformed/oddly-named spec lookup raises ``ValueError`` in some
    Python versions rather than returning ``None`` -- the probe stays
    tolerant of that so a weird environment degrades to "unavailable",
    never to an uncaught crash that would take the whole CLI down before a
    single node runs.
    """
    entry = _ADAPTER_REGISTRY.get(name)
    if entry is None:
        return False
    adapter_module, probe_module, _source = entry
    try:
        adapter_spec = importlib.util.find_spec(adapter_module)
    except (ImportError, ValueError, ModuleNotFoundError):
        adapter_spec = None
    if adapter_spec is None:
        return False
    if probe_module == adapter_module:
        return True
    try:
        lib_spec = importlib.util.find_spec(probe_module)
    except (ImportError, ValueError, ModuleNotFoundError):
        lib_spec = None
    return lib_spec is not None


def amplifier_agent_available() -> bool:
    """Back-compat alias -- see :func:`_worker_available`."""
    return _worker_available(AMPLIFIER_AGENT_NAME)


def _synthesize_agent_bundle_yaml(worker_name: str) -> str:
    """Return the minimal bundle YAML text wiring *worker_name*'s adapter as
    the run's spawn orchestrator.

    Generalizes the former amplifier-agent-only synthesis to any name in
    :data:`_ADAPTER_REGISTRY` -- this is the SAME synthesis used both for
    the availability-fallback default ladder (:func:`resolve`, worker=None)
    and for an EXPLICIT ``--worker loop-agent``/``--worker amplifier-agent``
    choice. Deliberately minimal: no ``module:`` on the top-level
    orchestrator (the runtime overlay ``_build_prepared`` composes on top
    always supplies one), no extra config keys the adapter doesn't read.
    This bundle text -- and the fact that a bundle is involved at all -- is
    entirely INTERNAL: it is never surfaced in the CLI, its help text, or
    any user-facing error (maintainer ruling: "bundles are under the hood").
    """
    adapter_module, _probe_module, adapter_source = _ADAPTER_REGISTRY[worker_name]
    providers = sorted(runner.PROVIDER_KEY_ENV)
    profile_lines = "\n".join(
        f"        {provider}: {DEFAULT_AGENT_NAME}" for provider in providers
    )
    return f"""\
bundle:
  name: dot-runner-{worker_name}-bundle
  version: 0.1.0
  description: >
    Auto-synthesized by dot-runner's named-worker resolution
    (amplifier_module_pipeline_runner.default_worker) for worker={worker_name!r}
    -- via --worker, a node's own worker= attribute, or (amplifier-agent only)
    the availability-fallback default ladder when no explicit choice was made.
    Wires the in-repo {adapter_module} adapter as the run's spawn orchestrator.
    Purely internal machinery -- never exposed as a user-facing concept.
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
      Pipeline-node worker for --worker {worker_name!r}, hosted via the
      {adapter_module} adapter (modules/{worker_name}).
    session:
      orchestrator:
        module: {worker_name}
        source: {adapter_source}
        config:
          llm_provider: anthropic
"""


def synthesize_default_agent_bundle_yaml() -> str:
    """Back-compat alias -- see :func:`_synthesize_agent_bundle_yaml`."""
    return _synthesize_agent_bundle_yaml(AMPLIFIER_AGENT_NAME)


def write_agent_bundle(worker_name: str) -> Path:
    """Write *worker_name*'s synthesized bundle YAML to a fresh temp file.

    A dedicated temp directory (mirrors ``cli.py``'s own
    ``tempfile.mkdtemp(prefix="dot-runner-run-")`` pattern for the default
    ``--logs-root``) -- independent of this run's own ``--logs-root``/
    ``--cwd`` so writing it never races their creation, and safe to call
    before either is resolved.
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix=f"dot-runner-{worker_name}-"))
    bundle_path = tmp_dir / "bundle.yaml"
    bundle_path.write_text(_synthesize_agent_bundle_yaml(worker_name), encoding="utf-8")
    return bundle_path


def write_default_agent_bundle() -> Path:
    """Back-compat alias -- see :func:`write_agent_bundle`."""
    return write_agent_bundle(AMPLIFIER_AGENT_NAME)


def resolve(
    *, worker: str | None, prog: str = "dot-runner"
) -> tuple[str | None, str | None]:
    """Resolve this run's effective ``(worker, bundle)`` pair.

    ``--bundle``/``DOT_RUNNER_BUNDLE`` are REMOVED from the CLI surface
    (WAVE 5 repair, maintainer ruling: "bundles are under the hood -- never
    exposed to runner users"). ``bundle`` in the returned pair is now purely
    INTERNAL machinery this function may synthesize itself; the caller
    (``cli.py``) never supplies one.

    Selection, in priority order:

    1. ``worker == "direct"`` -> returned as-is; the registry resolves it
       directly, no bundle involved.
    2. ``worker`` is a known named adapter (currently ``"loop-agent"`` or
       ``"amplifier-agent"``) -> probe its availability
       (:func:`_worker_available`). Available: synthesize + wire its minimal
       bundle (:func:`_synthesize_agent_bundle_yaml`) and return
       ``(None, bundle_path)`` (``worker`` stays ``None`` so
       ``run_pipeline``'s own ``elif bundle: resolved_worker =
       declared_worker`` branch reads ``"spawn"`` back from the synthesized
       bundle's own declared config). Unavailable: an EXPLICIT choice for a
       named-but-uninstalled worker fails loud rather than silently
       degrading to ``direct`` -- the caller asked for something specific.
    3. ``worker`` is anything else (unknown name, or ``None``/absent) -> if
       ``None``: no explicit choice was made, so attempt amplifier-agent as
       the default bet (available -> synthesize + wire, same as case 2;
       unavailable -> print ONE loud stderr line naming the upgrade path and
       return unchanged, letting the pre-existing bare-engine fallback chain
       resolve to ``direct`` exactly as it did before). If ``worker`` is a
       non-empty, unrecognized name: returned unchanged, letting the
       registry's own loud "Unknown worker" error fire downstream (existing
       behavior, now covering the two new names too).
    """
    if worker == "direct":
        return worker, None

    if worker is not None and worker in _ADAPTER_REGISTRY:
        if _worker_available(worker):
            return None, str(write_agent_bundle(worker))
        adapter_module, probe_module, _source = _ADAPTER_REGISTRY[worker]
        print(
            f"{prog}: --worker {worker!r} was requested but is not "
            f"installed (missing {adapter_module!r} and/or "
            f"{probe_module!r}). Install its dependencies, or choose "
            f"--worker direct" + (
                f" / --worker {AMPLIFIER_AGENT_NAME!r}"
                if worker == LOOP_AGENT_NAME
                else ""
            ) + ".",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if worker is not None:
        # Unknown name: hand back unchanged so the registry's own loud
        # "Unknown worker" error fires downstream (existing behavior).
        return worker, None

    # No explicit choice at all: attempt the amplifier-agent default bet.
    if _worker_available(AMPLIFIER_AGENT_NAME):
        return worker, str(write_agent_bundle(AMPLIFIER_AGENT_NAME))

    print(
        f"{prog}: no --worker given and amplifier-agent is not "
        f"installed -- falling back to worker=direct. Install the agent "
        f"extra for the default amplifier-agent worker: {UPGRADE_HINT}",
        file=sys.stderr,
    )
    return worker, None
