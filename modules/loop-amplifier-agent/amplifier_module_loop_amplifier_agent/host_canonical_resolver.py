"""Host-canonical module resolution for the in-process amplifier-agent child.

Why this exists
---------------
This adapter hosts an amplifier-agent ``Engine`` *inside the parent pipeline's
Python process*. The parent session and the hosted child session therefore
share one ``sys.modules``, but each resolves modules through a different
resolver: the parent's, and the agent's own prepared bundle (its own cache
clone of, e.g., ``context-simple``).

amplifier-core >= 2.0.1 refuses to import a module package from one path when
the same package name is already loaded from another
(``Refusing to import 'amplifier_module_x' from A: it is already loaded from
B``, ``amplifier_core/validation/base.py``). Before 2.0.1 the second copy
silently shadowed or was ignored, so the split was invisible.

``amplifier-app-cli`` avoids the same hazard for its spawned children by
mounting the parent's resolver on the child (``session_spawner.py``). This
adapter cannot do that wholesale -- the agent's bundle legitimately carries
modules the host never activated -- so :class:`HostCanonicalModuleResolver`
applies the same principle per module, in this order:

1. the copy this interpreter has ALREADY imported (canonical by definition);
2. the copy the host's resolver already has on disk (public
   ``get_module_source``; no fetch, no activation);
3. otherwise the agent's own resolver, unchanged (agent fallback).

Relation to app-cli: step 2 is the same principle (the child gets the parent's
module copy). Step 1 goes beyond app-cli -- it also covers modules imported
outside the host resolver (entry-point / editable installs) -- and step 3 keeps
the agent's own pinned sources instead of activating them lazily into the host.
Explicit source hints are resolved by the agent first. Identical checkouts may
reuse an already loaded copy (including surviving package descendants).
If the requested source is failed, unprepared, or conflicts with an already
loaded package, fail closed rather than silently substitute the host's copy.

Only public resolver APIs (``resolve`` / ``async_resolve`` /
``get_module_source``) are used; no resolver private attribute is read or
written. Known limits: a module whose package directory is not the conventional
``amplifier_module_<id>`` (core also accepts any ``amplifier_module_*`` dir) is
not recognized and falls back to the agent's copy; and ``_``-prefixed
attributes of the agent resolver (e.g. ``BundleModuleResolver._paths``) are
intentionally not forwarded by :meth:`HostCanonicalModuleResolver.__getattr__`.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from pathlib import Path
from typing import Any, NamedTuple

from amplifier_core.module_sources import ModuleSource

logger = logging.getLogger(__name__)

#: ``Reroute.reason`` values: which rule picked the canonical copy.
LOADED = "loaded"
HOST = "host"


class Reroute(NamedTuple):
    """A resolution redirected away from the agent's own copy of a module."""

    reason: str  # LOADED | HOST
    canonical_path: str
    agent_path: str | None


class _PathSource(ModuleSource):
    """``ModuleSource`` resolving to a fixed, pre-existing path."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def resolve(self) -> Path:
        return self._path

    def __str__(self) -> str:
        return f"HostCanonical({self._path})"


def _package_name(module_id: str) -> str:
    return "amplifier_module_" + module_id.replace("-", "_")


def _source_path(resolver: Any, module_id: str) -> Path | None:
    """``resolver.get_module_source(module_id)`` as a Path; never raises.

    A failing probe must not break the agent path -- it only means "no
    preference", so the caller falls back to the agent's own resolver.
    """
    getter = getattr(resolver, "get_module_source", None)
    if getter is None:
        return None
    try:
        raw = getter(module_id)
    except Exception:
        logger.debug("get_module_source(%r) failed", module_id, exc_info=True)
        return None
    return Path(raw) if raw else None


class HostCanonicalModuleResolver:
    """``ModuleSourceResolver`` that prefers host-canonical module copies.

    Lives for one turn (the adapter wraps a freshly prepared bundle per turn),
    so the per-module caches below cannot go stale or grow without bound.
    """

    def __init__(self, agent_resolver: Any, host_resolver: Any = None) -> None:
        self._agent = agent_resolver
        self._host = host_resolver
        self._host_cache: dict[str, Path | None] = {}
        #: module id -> first redirect away from the agent's own copy.
        self.reroutes: dict[str, Reroute] = {}
        #: module ids handed back to the agent resolver untouched.
        self.fallbacks: set[str] = set()

    # -- selection ---------------------------------------------------------

    def _loaded_root(self, module_id: str) -> Path | None:
        pkg = _package_name(module_id)
        roots: set[Path] = set()
        # Core validation may remove the package while leaving its imported
        # descendants. Those descendants still fix the process's module identity.
        for name, mod in list(sys.modules.items()):
            if name != pkg and not name.startswith(pkg + "."):
                continue
            file = getattr(mod, "__file__", None)
            if not file:
                continue
            for parent in Path(file).resolve().parents:
                if parent.name == pkg:
                    roots.add(parent.parent)
                    break
        if len(roots) > 1:
            raise RuntimeError(
                f"Module {module_id!r} is loaded from multiple roots: {sorted(map(str, roots))}"
            )
        return next(iter(roots), None)

    @staticmethod
    def _tree_identity(root: Path) -> str | None:
        """Bounded content identity, including source metadata and resources.

        An explicit hint is first resolved by the agent, so failed or unprepared
        sources still fail. Only an identical checkout can reuse a loaded copy.
        Different code, missing trees, symlinks and oversized trees fail closed.
        """
        digest = hashlib.sha256()
        total = count = directories = 0

        def fail_walk(error: OSError) -> None:
            raise error

        try:
            if not root.is_dir():
                return None
            for base, dirs, files in os.walk(root, onerror=fail_walk):
                directories += 1
                if directories > 2048:
                    return None
                dirs[:] = sorted(d for d in dirs if d not in {".git", "__pycache__"})
                if any((Path(base) / d).is_symlink() for d in dirs):
                    return None
                for name in sorted(files):
                    # Bytecode outside __pycache__ may be the only executable
                    # source of a module; include it in checkout identity.
                    path = Path(base) / name
                    # Foundation writes checkout bookkeeping here (timestamp,
                    # floating ref vs resolved commit), not module content.
                    if Path(base) == root and name == ".amplifier_cache_meta.json":
                        continue
                    if path.is_symlink() or not path.is_file():
                        return None
                    file_stat = path.stat()
                    count += 1
                    total += file_stat.st_size
                    if count > 2048 or total > 16 * 1024 * 1024:
                        return None
                    digest.update(str(path.relative_to(root)).encode())
                    digest.update(b"\0")
                    # Git preserves executability; equal resource bytes alone
                    # do not imply a packaged helper can execute in both copies.
                    digest.update((file_stat.st_mode & 0o111).to_bytes(2, "big"))
                    digest.update(hashlib.sha256(path.read_bytes()).digest())
            return digest.hexdigest() if count else None
        except OSError:
            return None

    def _canonical_for_hint(self, module_id: str, source: Any) -> Any:
        root = self._loaded_root(module_id)
        if root is None:
            return source
        agent_root = Path(source.resolve()).resolve()
        if agent_root == root:
            return source
        identity = self._tree_identity(agent_root)
        if identity is not None and identity == self._tree_identity(root):
            return self._pick(module_id)
        return source

    def _host_root(self, module_id: str) -> Path | None:
        # Memoized: the host may be app-cli's resolver, whose misses fall
        # through to an uncached settings-file read on every lookup.
        if module_id not in self._host_cache:
            base = _source_path(self._host, module_id)
            # ``is_dir`` also filters non-path sources (e.g. ``git+https://``
            # URIs app-cli's settings resolver returns) -- do not drop it.
            usable = base is not None and (base / _package_name(module_id)).is_dir()
            self._host_cache[module_id] = base.resolve() if usable else None
        return self._host_cache[module_id]

    def _pick(self, module_id: str) -> _PathSource | None:
        root = self._loaded_root(module_id)
        reason = LOADED
        if root is None:
            root = self._host_root(module_id)
            reason = HOST
        if root is None:
            self.fallbacks.add(module_id)
            return None
        if module_id not in self.reroutes:
            agent = _source_path(self._agent, module_id)
            agent_path = str(agent.resolve()) if agent is not None else None
            if agent_path != str(root):  # identical copy: not a reroute
                self.reroutes[module_id] = Reroute(reason, str(root), agent_path)
                logger.info(
                    "loop-amplifier-agent: module %r -> host-canonical %s (%s); "
                    "agent copy %s",
                    module_id,
                    root,
                    reason,
                    agent_path,
                )
        return _PathSource(root)

    # -- ModuleSourceResolver protocol ------------------------------------

    def resolve(
        self, module_id: str, source_hint: Any = None, profile_hint: Any = None
    ) -> Any:
        if source_hint is not None or profile_hint is not None:
            source = self._agent.resolve(
                module_id, source_hint=source_hint, profile_hint=profile_hint
            )
            return self._canonical_for_hint(module_id, source)
        picked = self._pick(module_id)
        if picked is not None:
            return picked
        return self._agent.resolve(
            module_id, source_hint=source_hint, profile_hint=profile_hint
        )

    async def async_resolve(
        self, module_id: str, source_hint: Any = None, profile_hint: Any = None
    ) -> Any:
        if source_hint is not None or profile_hint is not None:
            fn = getattr(self._agent, "async_resolve", None)
            if fn is not None:
                source = await fn(
                    module_id, source_hint=source_hint, profile_hint=profile_hint
                )
            else:
                source = self._agent.resolve(
                    module_id, source_hint=source_hint, profile_hint=profile_hint
                )
            return self._canonical_for_hint(module_id, source)
        picked = self._pick(module_id)
        if picked is not None:
            return picked
        fn = getattr(self._agent, "async_resolve", None)
        if fn is not None:
            return await fn(
                module_id, source_hint=source_hint, profile_hint=profile_hint
            )
        return self._agent.resolve(
            module_id, source_hint=source_hint, profile_hint=profile_hint
        )

    def get_module_source(self, module_id: str) -> str | None:
        root = self._loaded_root(module_id) or self._host_root(module_id)
        if root is not None:
            return str(root)
        getter = getattr(self._agent, "get_module_source", None)
        return getter(module_id) if getter else None

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes not defined here: preserve the agent
        # resolver's public surface for any consumer that probes it. Private
        # (``_``) names are NOT forwarded -- this also keeps unpickling /
        # copy from recursing before ``_agent`` exists.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._agent, name)

    @property
    def agent_resolver(self) -> Any:
        return self._agent


def install_host_canonical_resolver(prepared: Any, host_resolver: Any = None) -> Any:
    """Wrap ``prepared.resolver`` (public dataclass field).

    Never double-wraps: a repeat call re-wraps the original agent resolver
    with the (possibly different) host resolver. Returns the installed
    wrapper, or ``None`` when ``prepared`` exposes no resolver (nothing to
    protect).
    """
    current = getattr(prepared, "resolver", None)
    if current is None:
        return None
    if isinstance(current, HostCanonicalModuleResolver):
        current = current.agent_resolver
    wrapper = HostCanonicalModuleResolver(current, host_resolver)
    prepared.resolver = wrapper
    return wrapper


def runtime_identity() -> dict[str, str]:
    """Concise identity of the runtime hosting the child (for logs/tests)."""
    ident: dict[str, str] = {"python": sys.version.split()[0]}
    try:
        import amplifier_core

        ident["amplifier_core"] = str(getattr(amplifier_core, "__version__", "unknown"))
        ident["amplifier_core_path"] = str(Path(amplifier_core.__file__).parent)
    except Exception:  # noqa: BLE001
        ident["amplifier_core"] = "unavailable"
    return ident


_identity_logged = False


def log_runtime_identity_once() -> None:
    """INFO-log :func:`runtime_identity` once per process (not per node turn)."""
    global _identity_logged
    if _identity_logged:
        return
    _identity_logged = True
    logger.info("loop-amplifier-agent runtime identity: %s", runtime_identity())
