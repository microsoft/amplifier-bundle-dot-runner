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

WAVE 6 (feat/agent-always-installed) ruling: amplifier-agent is no longer an
optional, probed-for peer -- the root ``amplifier-dot-runner`` package now
declares ``amplifier-module-loop-amplifier-agent`` (and, transitively,
amplifier-agent's own ``amplifier_agent_lib``) as a REAL, unconditional
dependency (see root ``pyproject.toml``). The default ladder, when a run
makes NO explicit worker choice (no ``--worker``, no node ``worker=``), is
now exactly this, PERIOD:

    explicit --worker / node worker= > amplifier-agent

There is no third rung. :func:`resolve` still does the wiring:

1. Synthesize a MINIMAL bundle (:func:`_synthesize_agent_bundle_yaml`)
   declaring one agent entry backed by ``loop-amplifier-agent`` (the same
   synthesis this module uses for an EXPLICIT ``--worker loop-agent``/
   ``--worker amplifier-agent`` choice too -- see :func:`resolve` -- just
   parametrized by which adapter module to wire), with a top-level
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

2. :func:`_worker_available` (cheap ``importlib.util.find_spec`` check --
   locates a module without importing it, so it never pays amplifier-agent's
   heavy transitive import cost, and never touches the network) is now a
   RUNTIME IMPORT GUARD ONLY, not an availability probe with an "install
   this to unlock the feature" upgrade story. In a healthy always-installed
   environment it is always ``True`` and this guard never fires. If it is
   ever ``False`` here, that means amplifier-agent genuinely failed to
   import despite being an unconditional dependency of this install -- an
   ABNORMAL, broken-environment state (a stale/partial venv, a corrupted
   cache, a hand-edited site-packages), not a legitimate "not installed by
   choice" state anymore. :func:`resolve` degrades to ``direct`` in that
   case, but prints a LOUD stderr line diagnosing the broken install and
   naming the reinstall command (:data:`BROKEN_INSTALL_HINT`) -- reworded
   from the old upgrade pitch, which no longer applies now that there is
   nothing left to "upgrade" into.

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
#: name -> (adapter python package, probe module, git source, REGISTERED
#: orchestrator module name). The 4th element is the name the adapter
#: registers under its own ``[project.entry-points."amplifier.modules"]``
#: table (e.g. modules/loop-amplifier-agent/pyproject.toml registers
#: ``loop-amplifier-agent = amplifier_module_loop_amplifier_agent:mount``) --
#: it is NOT always identical to the worker name a user types after
#: --worker. In particular ``--worker amplifier-agent`` is hosted by the
#: ``loop-amplifier-agent`` module: reusing the worker name itself as the
#: synthesized bundle's ``session.orchestrator.module`` would declare a
#: module that does not exist and fail to mount at spawn time. loop-agent's
#: worker name happens to equal its module name; that coincidence must not
#: be baked in as an assumption for future registry entries.
_ADAPTER_REGISTRY: dict[str, tuple[str, str, str, str]] = {
    LOOP_AGENT_NAME: (
        "amplifier_module_loop_agent",
        "amplifier_module_loop_agent",
        (
            "git+https://github.com/microsoft/amplifier-bundle-dot-runner@main"
            "#subdirectory=modules/loop-agent"
        ),
        "loop-agent",
    ),
    AMPLIFIER_AGENT_NAME: (
        "amplifier_module_loop_amplifier_agent",
        "amplifier_agent_lib",
        (
            "git+https://github.com/microsoft/amplifier-bundle-dot-runner@main"
            "#subdirectory=modules/loop-amplifier-agent"
        ),
        "loop-amplifier-agent",
    ),
}

#: Name of the single synthesized agent entry every known provider routes to.
DEFAULT_AGENT_NAME = "dot-runner-default-agent"

#: Broken-install hint printed on the ONE stderr fallback line when the
#: runtime import guard trips (WAVE 6: amplifier-agent is now an
#: unconditional dependency of the root install -- see root pyproject.toml
#: -- so this is no longer an "install this optional extra" upgrade pitch.
#: There is nothing left to opt into: if this fires, the environment's
#: install of an ALWAYS-declared dependency is broken, and the fix is a
#: reinstall, not a new install command). Mirrors compat.py's
#: ``IncompatibleEngineError`` reinstall instruction for the same reason:
#: both are "this environment is stale/broken" diagnostics, not feature
#: discovery.
BROKEN_INSTALL_HINT = (
    "amplifier-agent ships as an unconditional dependency of this install "
    "and could not be imported -- this environment is broken (stale cache, "
    "partial install, or a hand-edited venv); reinstall: `uv tool install "
    "--reinstall git+https://github.com/microsoft/amplifier-bundle-dot-runner` "
    "(or, from the repo tree, `cd modules/pipeline-runner && uv sync --reinstall`)"
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

#: Canonical provider name (``unified_llm.client.PROVIDER_ENV_KEYS`` /
#: ``runner.PROVIDER_KEY_ENV``) -> the reference provider MODULE's git source.
#: This is the exact ``module:``/``source:`` shape amplifier-bundle-attractor's
#: own ``bundles/attractor-pipeline.yaml`` declared under its top-level
#: ``providers:`` section before 0.2.0 retired ``--bundle``/``DOT_RUNNER_BUNDLE``
#: (issue #338) -- e.g. ``{module: provider-anthropic, source: git+https://
#: github.com/microsoft/amplifier-module-provider-anthropic@main}``.
#:
#: Resolved via runtime git+fetch (``ModuleActivator.activate_all``, triggered
#: because each entry below carries a ``source:`` key), the SAME mechanism
#: this synthesis already uses for every other module it wires (the adapter
#: itself, loop-agent/loop-amplifier-agent, and context-simple) -- not
#: installed-package/entry-point resolution. Chosen deliberately over adding
#: these three provider modules as unconditional root dependencies: none of
#: them is a root dependency of amplifier-dot-runner today (unlike the
#: worker adapters, which point 2 of issue #338 makes root deps because a
#: NAMED WORKER must always resolve), a run typically configures only one of
#: the three providers, and pinning ``@main`` here matches exactly what the
#: old attractor bundle did -- deterministic, and proven by the live gate
#: (see issue #338 report) rather than assumed.
_PROVIDER_MODULE_SOURCES: dict[str, str] = {
    "anthropic": "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main",
    "openai": "git+https://github.com/microsoft/amplifier-module-provider-openai@main",
    "gemini": "git+https://github.com/microsoft/amplifier-module-provider-gemini@main",
}

#: Tool modules every synthesized named-worker bundle mounts.
#:
#: LIVE-GATE FINDING (amplifier-bundle-attractor run 33296437678). Issue #338
#: fixed the PROVIDER half of this synthesis -- a spawned box-node worker can
#: finally reach a model -- but the synthesis still emitted no ``tools:``
#: section at all, so that worker reached the model with NO file, shell, or
#: search tools mounted. The engine's own transcript is the proof
#: (``logs/orient/response.md`` of that run, the model speaking for itself):
#:
#:     "My actual available tool set in this session is limited to
#:      `spawn_agent`, `send_input`, `wait`, and `close_agent` -- there is no
#:      `read_file`, `write_file`, `edit_file`, `shell`, `grep`, or `glob`
#:      tool actually exposed to me, despite the system prompt describing
#:      them as if available."
#:
#: Consequence: EVERY ``must_write=`` node contract is unsatisfiable by
#: construction on the named-worker path. That run's first maker node
#: (``orient``, ``must_write=".ai/brief.md"``) burned its whole retry budget
#: across 210s of real Anthropic calls without writing one byte, and the
#: pipeline died pre-loop at ``orient_fail``. A worker that can think but
#: cannot act is not a worker -- and the failure is silent at mount time,
#: surfacing only as an exhausted contract minutes later.
#:
#: THE SET IS NOT INVENTED. It is exactly what amplifier-bundle-attractor's
#: own ``bundles/attractor-pipeline.yaml`` declared under its top-level
#: ``tools:`` section -- the SAME proven reference #338's
#: ``_PROVIDER_MODULE_SOURCES`` was derived from, and the composition that
#: demonstrably drove these pipelines before 0.2.0 retired
#: ``--bundle``/``DOT_RUNNER_BUNDLE``. Both halves of that bundle's mount plan
#: (providers AND tools) are reproduced here; the synthesis had silently
#: dropped both, and #338 restored only one.
#:
#: Mounted TOP-LEVEL (session-wide) rather than per-agent, mirroring the
#: reference bundle: spawned child agents inherit the parent session's tool
#: surface, so one declaration serves the orchestrator session and every
#: ``profiles:``-routed agent alike.
#:
#: Resolved via runtime git+fetch (``ModuleActivator.activate_all``, keyed off
#: the ``source:`` each entry carries) -- the same mechanism this synthesis
#: already uses for the adapter, the providers, and context-simple. No config
#: overrides are emitted: the reference bundle's ``timeout: 120`` on tool-bash
#: was attractor-side policy, and the equivalent knob on this path is
#: loop-agent's own ``default_command_timeout_ms``. The engine mounts the
#: capability; policy stays with the caller.
_TOOL_MODULE_SOURCES: dict[str, str] = {
    "tool-filesystem": "git+https://github.com/microsoft/amplifier-module-tool-filesystem@main",
    "tool-bash": "git+https://github.com/microsoft/amplifier-module-tool-bash@main",
    "tool-search": "git+https://github.com/microsoft/amplifier-module-tool-search@main",
}


def _detect_configured_providers() -> list[str]:
    """Canonical provider names with a configured API key, in priority order.

    Delegates to ``unified_llm.client.detect_configured_providers`` -- the
    SAME single source of truth ``runner._bootstrap_direct_provider`` uses
    (via ``unified_llm.Client.from_env()``) for the ``direct`` worker's own
    provider bootstrap (ANTHROPIC_API_KEY / OPENAI_API_KEY / GEMINI_API_KEY,
    GOOGLE_API_KEY as a Gemini alias). One source of truth, imported here
    rather than re-declared -- issue #338's root cause was exactly a bundle
    author's list (``bundles/attractor-pipeline.yaml``) silently going stale
    relative to the engine's own; a second hand-copied env-var list in this
    module would reintroduce the same class of drift.

    Imported lazily (inside this function, not at module top level) so that
    importing ``default_worker`` never requires ``unified-llm-client`` to be
    importable before it is actually needed -- mirrors
    ``runner._bootstrap_direct_provider``'s own ``import unified_llm`` placement.
    """
    from unified_llm.client import detect_configured_providers

    return detect_configured_providers()


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
    adapter_module, probe_module, _source, _orch_module = entry
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
    adapter_module, _probe_module, adapter_source, orch_module = _ADAPTER_REGISTRY[
        worker_name
    ]
    providers = sorted(runner.PROVIDER_KEY_ENV)
    profile_lines = "\n".join(
        f"        {provider}: {DEFAULT_AGENT_NAME}" for provider in providers
    )

    # issue #338 fix: mount a REAL provider module for every provider the
    # environment has a key for -- previously this synthesis emitted a
    # `profiles:` routing map (above) but NO top-level `providers:` section
    # at all, so a spawned loop-agent/loop-amplifier-agent child always saw
    # `providers={}` and died on "Available providers: []" the instant any
    # box node dispatched (issue #338), even with a valid API key present.
    configured = _detect_configured_providers()
    if not configured:
        supported = ", ".join(
            f"{k}={v}" for k, v in sorted(runner.PROVIDER_KEY_ENV.items())
        )
        raise runner.NoProviderConfiguredError(
            f"--worker {worker_name!r} needs a mounted LLM provider, but no "
            "supported API key is configured in the environment. Set one of "
            f"the following environment variables and retry: {supported} "
            "(gemini also accepts GOOGLE_API_KEY). This is the SAME "
            "fail-loud check the `direct` worker's own provider bootstrap "
            "uses (runner._bootstrap_direct_provider) -- never a silent "
            "empty provider mount."
        )
    provider_lines = "\n".join(
        f"  - module: provider-{name}\n    source: {_PROVIDER_MODULE_SOURCES[name]}"
        for name in configured
    )

    # Live-gate fix (see _TOOL_MODULE_SOURCES): mount a REAL tool surface.
    # #338 restored the providers half of this synthesis; without this half a
    # box-node worker reaches the model with only the spawn tools and cannot
    # read, write, or run anything -- so every `must_write=` contract is
    # unsatisfiable by construction. Unconditional (unlike providers, which
    # are keyed off configured API keys): there is no environment in which a
    # pipeline worker is better off unable to touch the tree it was pointed at.
    tool_lines = "\n".join(
        f"  - module: {name}\n    source: {source}"
        for name, source in _TOOL_MODULE_SOURCES.items()
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
providers:
{provider_lines}
tools:
{tool_lines}
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
        module: {orch_module}
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
       ``"amplifier-agent"``) -> the runtime import guard
       (:func:`_worker_available`) checks it resolves. Available: synthesize
       + wire its minimal bundle (:func:`_synthesize_agent_bundle_yaml`) and
       return ``(None, bundle_path)`` (``worker`` stays ``None`` so
       ``run_pipeline``'s own ``elif bundle: resolved_worker =
       declared_worker`` branch reads ``"spawn"`` back from the synthesized
       bundle's own declared config). Unavailable: an EXPLICIT choice for a
       named worker fails loud rather than silently degrading to ``direct``
       -- the caller asked for something specific. For ``loop-agent`` (a
       genuinely optional worker, never embedded at the root) this means
       "install its dependencies"; for ``amplifier-agent`` (WAVE 6: an
       unconditional dependency of the root install) this means the
       environment's install is broken -- see :data:`BROKEN_INSTALL_HINT`.
    3. ``worker`` is anything else (unknown name, or ``None``/absent) -> if
       ``None``: no explicit choice was made, so the ONLY rung left is
       amplifier-agent, PERIOD (available -> synthesize + wire, same as
       case 2; unavailable -> print ONE loud stderr line diagnosing the
       broken install and return unchanged, letting the pre-existing
       bare-engine fallback chain resolve to ``direct`` exactly as it did
       before). If ``worker`` is a non-empty, unrecognized name: returned
       unchanged, letting the registry's own loud "Unknown worker" error
       fire downstream (existing behavior, now covering the two new names
       too).
    """
    if worker == "direct":
        return worker, None

    if worker is not None and worker in _ADAPTER_REGISTRY:
        if _worker_available(worker):
            return None, str(write_agent_bundle(worker))
        adapter_module, probe_module, _source, _orch_module = _ADAPTER_REGISTRY[worker]
        if worker == AMPLIFIER_AGENT_NAME:
            # WAVE 6: amplifier-agent is an unconditional dependency of the
            # root install -- an explicit ask for it failing the runtime
            # import guard is a broken environment, not a missing optional
            # extra. Same diagnostic as the no-explicit-choice branch below.
            print(
                f"{prog}: --worker {worker!r} was requested, but {BROKEN_INSTALL_HINT}",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print(
            f"{prog}: --worker {worker!r} was requested but is not "
            f"installed (missing {adapter_module!r} and/or "
            f"{probe_module!r}). Install its dependencies, or choose "
            f"--worker direct / --worker {AMPLIFIER_AGENT_NAME!r}.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    if worker is not None:
        # Unknown name: hand back unchanged so the registry's own loud
        # "Unknown worker" error fires downstream (existing behavior).
        return worker, None

    # No explicit choice at all: the ladder has exactly one rung left --
    # amplifier-agent, PERIOD (WAVE 6: no longer a "bet", the unconditional
    # default). The runtime import guard is normally a no-op (always True in
    # a healthy always-installed environment); if it trips here, that is an
    # abnormal, broken-install state, not a legitimate "not installed by
    # choice" state.
    if _worker_available(AMPLIFIER_AGENT_NAME):
        return worker, str(write_agent_bundle(AMPLIFIER_AGENT_NAME))

    print(
        f"{prog}: no --worker given -- falling back to worker=direct. "
        f"{BROKEN_INSTALL_HINT}",
        file=sys.stderr,
    )
    return worker, None
