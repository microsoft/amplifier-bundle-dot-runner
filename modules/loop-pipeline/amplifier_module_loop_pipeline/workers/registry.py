"""``WorkerRegistry`` -- named registration/resolution below Sec4.5's seam.

DESIGN-worker-registry-core-split.md P1 item 1: "Workers are registered by
NAME (registry keys names, not source trees...)". Gap-table rows 6/7:
"every registered worker clones, or declares absence loudly" -- enforced
here at *registration* time, not per-call ``hasattr``/``getattr`` guessing.

Unknown-name resolution is loud by design (never a silent fallback): the
test surface (`tests/test_worker_registry.py`) pins this. ``AmplifierBackend``
additionally recognizes the reserved sentinel name ``"spawn"`` (the hosted
``session.spawn`` path, resolved via the pre-existing ``profiles`` map --
never a registry ``Worker`` instance; see that module's
``_KNOWN_WORKER_SENTINELS``) when composing the full "known worker names"
set surfaced in selection-time error messages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .worker_protocol import Worker

_REQUIRED_MEMBERS = ("run", "clone", "close")

#: WAVE 7 (feat/fail-loud-worker-names, 2026-08-30, maintainer ruling): the
#: user-facing worker NAMES were renamed -- ``direct`` -> ``llm-direct`` (the
#: bare loop on the unified-llm-spec client) and ``loop-agent`` ->
#: ``coding-agent`` (it implements the coding-agent-loop spec). NO aliases
#: for the old names -- this is a band-aid rip, not a compat shim. This map
#: exists ONLY so an old name's "Unknown worker" error can name its
#: replacement as a migration hint, kept for a release or two (see
#: specs/EXTENSIONS.md Sec40's dated rename note) then deleted outright.
RENAMED_WORKER_NAMES: dict[str, str] = {
    "direct": "llm-direct",
    "loop-agent": "coding-agent",
}


class WorkerRegistry:
    """Holds constructed ``Worker`` instances, keyed by name."""

    def __init__(self, workers: dict[str, Worker] | None = None) -> None:
        self._workers: dict[str, Worker] = {}
        for name, worker in (workers or {}).items():
            self.register(name, worker)

    def register(self, name: str, worker: Worker) -> None:
        """Register *worker* under *name*.

        Fails loud (``TypeError``) if the worker does not expose all of
        ``run``/``clone``/``close`` -- gap-table rows 6/7's "clones, or
        declares absence loudly" is enforced HERE, once, rather than at
        every call site via a ``hasattr`` guard (the exact pattern the
        design doc's row 6 cites as the silent-failure mode it closes:
        ``handlers/__init__.py``'s pre-existing ``hasattr(backend, "clone")``
        guard around ``DirectProviderBackend``, which had none).
        """
        missing = [m for m in _REQUIRED_MEMBERS if not hasattr(worker, m)]
        if missing:
            raise TypeError(
                f"Worker {name!r} ({type(worker).__name__}) is missing "
                f"required member(s) {missing} -- every registered worker "
                f"must implement run()/clone()/close() (DESIGN-worker-"
                f"registry-core-split.md gap-table rows 6/7). Declare the "
                f"absence loudly (do not register) instead of registering "
                f"a partial worker."
            )
        self._workers[name] = worker

    def resolve(self, name: str) -> Worker:
        """Return the worker registered under *name*.

        Raises ``ValueError`` naming every registered worker when *name* is
        unknown -- never a silent fallback (P1 test discipline item 1). A
        renamed old name (see :data:`RENAMED_WORKER_NAMES`) gets an extra
        ``renamed:`` clause naming its replacement -- the error message IS
        the migration hint, not an alias that would keep the old name
        working.
        """
        try:
            return self._workers[name]
        except KeyError:
            hint = ""
            renamed_to = RENAMED_WORKER_NAMES.get(name)
            if renamed_to is not None:
                hint = f" (renamed: {name!r} -> {renamed_to!r})"
            raise ValueError(
                f"Unknown worker {name!r}{hint}. Registered workers: {sorted(self._workers)}."
            ) from None

    def names(self) -> frozenset[str]:
        return frozenset(self._workers)

    def clone(self) -> WorkerRegistry:
        """Return a new registry whose workers are each branch-isolated
        clones (see ``Worker.clone()``) -- parallel branches must never
        share worker-held mutable state (EXTENSIONS.md Sec9/Sec13)."""
        return WorkerRegistry({name: w.clone() for name, w in self._workers.items()})

    async def close_all(self) -> None:
        """Close every registered worker (spec finalize contract)."""
        for worker in self._workers.values():
            await worker.close()
