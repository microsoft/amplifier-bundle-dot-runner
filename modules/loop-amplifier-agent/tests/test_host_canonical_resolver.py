"""Real-core regression tests for host-canonical module resolution.

No mocks at the boundary that matters: duplicate module packages live on
disk, and the REAL amplifier-core session/loader/validation (>= 2.0.1's
"Refusing to import" guard) initializes host and child sessions that share
one ``sys.modules`` -- exactly the in-process amplifier-agent hosting shape.
"""

from __future__ import annotations

import importlib.metadata
import logging
import sys
import types
import uuid
from pathlib import Path
from typing import Any

import amplifier_module_loop_amplifier_agent as laa
import amplifier_module_loop_amplifier_agent.host_canonical_resolver as hcr
import pytest
from amplifier_core import AmplifierSession
from amplifier_foundation.bundle._prepared import BundleModuleResolver
from amplifier_module_loop_amplifier_agent.host_canonical_resolver import (
    HOST,
    LOADED,
    HostCanonicalModuleResolver,
    install_host_canonical_resolver,
    runtime_identity,
)

from ._fakes import CapturingHooks, make_fake_deps

CTX = """
COPY = {copy!r}
class Ctx:
    copy = COPY
    def __init__(self): self.m = []
    async def add_message(self, m): self.m.append(m)
    async def get_messages(self): return list(self.m)
    async def get_messages_for_request(self, token_budget=None, provider=None): return list(self.m)
    async def set_messages(self, m): self.m = list(m)
    async def clear(self): self.m = []
async def mount(coordinator, config=None):
    await coordinator.mount("context", Ctx())
    return None
"""
ORCH = """
class O:
    async def execute(self, prompt, context, providers, tools, hooks, **kw):
        return type(context).copy
async def mount(coordinator, config=None):
    await coordinator.mount("orchestrator", O())
    return None
"""


def _write(root: Path, copy: str, mid: str, body: str) -> None:
    pkg = (
        root
        / copy
        / f"amplifier-module-{mid}"
        / f"amplifier_module_{mid.replace('-', '_')}"
    )
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(body)


class Env:
    def __init__(self, root: Path, tag: str) -> None:
        self.root = root
        self.ctx = f"context-{tag}"
        self.loop = f"loop-{tag}"
        self.only = f"context-only{tag}"  # exists solely in the agent copy
        # In both copies, but never imported by the host session's plan.
        self.extra = f"context-extra{tag}"
        for copy in ("host", "agent"):
            _write(root, copy, self.ctx, CTX.format(copy=copy))
            _write(root, copy, self.loop, ORCH)
            _write(root, copy, self.extra, CTX.format(copy=copy))
        _write(root, "agent", self.only, CTX.format(copy="agent-only"))
        self.plan = {
            "session": {"orchestrator": self.loop, "context": self.ctx},
            "providers": [],
            "tools": [],
            "hooks": [],
        }

    def resolver(self, copy: str) -> BundleModuleResolver:
        mids = [self.ctx, self.loop, self.extra] + (
            [self.only] if copy == "agent" else []
        )
        return BundleModuleResolver(
            {m: self.root / copy / f"amplifier-module-{m}" for m in mids}
        )

    def _plan_with_context(self, mid: str) -> dict[str, Any]:
        return {**self.plan, "session": {**self.plan["session"], "context": mid}}

    def plan_only(self) -> dict[str, Any]:
        return self._plan_with_context(self.only)

    def plan_extra(self) -> dict[str, Any]:
        return self._plan_with_context(self.extra)

    async def host_session(self) -> AmplifierSession:
        s = AmplifierSession(dict(self.plan))
        await s.coordinator.mount("module-source-resolver", self.resolver("host"))
        await s.initialize()
        return s

    async def child(self, resolver: Any, plan: dict[str, Any] | None = None):
        c = AmplifierSession(dict(plan or self.plan))
        await c.coordinator.mount("module-source-resolver", resolver)
        await c.initialize()
        return c


@pytest.fixture
def env(tmp_path: Path):
    tag = uuid.uuid4().hex[:8]
    e = Env(tmp_path, tag)
    before_path = list(sys.path)
    yield e
    for name in [n for n in sys.modules if tag in n]:
        del sys.modules[name]
    sys.path[:] = before_path


def _chain(exc: BaseException) -> str:
    parts = []
    while exc is not None:
        parts.append(str(exc))
        exc = exc.__cause__ or exc.__context__
    return " | ".join(parts)


def test_runtime_identity_reports_core():
    ident = runtime_identity()
    assert ident["amplifier_core"] == importlib.metadata.version("amplifier-core")
    assert ident["amplifier_core_path"]


@pytest.mark.asyncio
async def test_negative_control_agent_resolver_alone_is_refused(env: Env):
    """Without the wrapper the child hits core's duplicate-package guard."""
    await env.host_session()
    with pytest.raises(Exception) as ei:
        await env.child(env.resolver("agent"))
    assert "Refusing to import" in _chain(ei.value)


@pytest.mark.asyncio
async def test_wrapper_reuses_host_loaded_copy(env: Env):
    host = await env.host_session()
    w = HostCanonicalModuleResolver(
        env.resolver("agent"), host.coordinator.get("module-source-resolver")
    )
    child = await env.child(w)
    assert await child.execute("go") == "host"
    assert w.reroutes[env.ctx].reason == LOADED
    assert not w.fallbacks


@pytest.mark.asyncio
async def test_wrapper_falls_back_to_agent_for_agent_only_module(env: Env):
    """Agent fallback preserved: nothing host-side exists for this module."""
    await env.host_session()
    w = HostCanonicalModuleResolver(env.resolver("agent"), env.resolver("host"))
    # loop-<tag> is host-canonical; the context is agent-only.
    child = await env.child(w, env.plan_only())
    assert await child.execute("go") == "agent-only"
    assert env.only in w.fallbacks
    assert env.only not in w.reroutes


def test_host_resolver_step_without_prior_import(env: Env):
    """Not yet imported, but the host resolver has it on disk: host wins."""
    w = HostCanonicalModuleResolver(env.resolver("agent"), env.resolver("host"))
    src = w.resolve(env.ctx)
    assert (
        Path(src.resolve())
        == (env.root / "host" / f"amplifier-module-{env.ctx}").resolve()
    )
    assert w.reroutes[env.ctx].reason == HOST
    # unknown-to-host module: agent's own answer, unchanged
    assert (
        w.resolve(env.only).resolve()
        == env.resolver("agent").resolve(env.only).resolve()
    )


@pytest.mark.parametrize("failure", ["failed", "unprepared"])
@pytest.mark.asyncio
async def test_explicit_provider_source_failure_is_not_replaced_by_host(
    env: Env, failure: str
):
    """A valid but unimported host copy must not mask source B's refusal."""

    class SourceAwareAgent:
        def __init__(self):
            self.prepared = {"provider-A"}
            self.failed = {"provider-B"} if failure == "failed" else set()

        def resolve(self, module_id, source_hint=None, profile_hint=None):
            assert module_id == env.ctx
            assert source_hint == "provider-B"
            if source_hint in self.failed:
                raise ValueError("provider-B failed")
            if source_hint not in self.prepared:
                raise ValueError("provider-B unprepared")
            return env.resolver("agent").resolve(module_id)

        async def async_resolve(self, module_id, source_hint=None, profile_hint=None):
            return self.resolve(module_id, source_hint, profile_hint)

    agent = SourceAwareAgent()
    host = env.resolver("host")
    w = HostCanonicalModuleResolver(agent, host)
    assert w._host_root(env.ctx) is not None  # source A is available on disk
    assert w._loaded_root(env.ctx) is None
    with pytest.raises(ValueError, match=f"provider-B {failure}"):
        agent.resolve(env.ctx, source_hint="provider-B")
    with pytest.raises(ValueError, match=f"provider-B {failure}"):
        w.resolve(env.ctx, source_hint="provider-B")
    with pytest.raises(ValueError, match=f"provider-B {failure}"):
        await w.async_resolve(env.ctx, source_hint="provider-B")
    assert not w.reroutes
    assert not w.fallbacks


@pytest.mark.asyncio
async def test_explicit_source_selects_agent_even_if_host_copy_exists(env: Env):
    class SourceAwareAgent:
        def resolve(self, module_id, source_hint=None, profile_hint=None):
            assert source_hint == "provider-B"
            return env.resolver("agent").resolve(module_id)

        async def async_resolve(self, module_id, source_hint=None, profile_hint=None):
            return self.resolve(module_id, source_hint, profile_hint)

    w = HostCanonicalModuleResolver(SourceAwareAgent(), env.resolver("host"))
    expected = env.resolver("agent").resolve(env.ctx).resolve()
    assert w.resolve(env.ctx, source_hint="provider-B").resolve() == expected
    assert (
        await w.async_resolve(env.ctx, source_hint="provider-B")
    ).resolve() == expected
    assert not w.reroutes


def test_install_never_double_wraps_and_skips_resolverless_prepared(env: Env):
    class P:
        resolver = env.resolver("agent")

    p = P()
    original = p.resolver
    first = install_host_canonical_resolver(p)
    second = install_host_canonical_resolver(p)
    assert isinstance(p.resolver, HostCanonicalModuleResolver)
    assert second is p.resolver and second is not first
    assert first.agent_resolver is original
    assert second.agent_resolver is original  # never double-wrapped

    class NoResolver:
        pass

    assert install_host_canonical_resolver(NoResolver()) is None


@pytest.mark.asyncio
async def test_adapter_run_turn_installs_wrapper_before_real_session_init(
    env: Env, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """Real adapter code path (`execute` -> `_run_turn`) + real core init.

    Only amplifier-agent's Engine/bundle plumbing is faked (as in every other
    adapter test). `create_session` is replaced by one that initializes a REAL
    AmplifierSession using whatever `prepared.resolver` the adapter left in
    place -- so this fails with "Refusing to import" if the adapter does not
    install the wrapper before session creation.
    """
    host = await env.host_session()
    # The host session's mounted resolver knows env.extra (never imported):
    # only reachable if the adapter hands the coordinator's resolver over.
    monkeypatch.setattr(hcr, "_identity_logged", False)
    deps, captured = make_fake_deps(reply_text="ok")
    prepared = captured["prepared"]
    prepared.resolver = env.resolver("agent")
    fake_session = captured["session"]
    seen: dict[str, Any] = {}

    async def real_create_session(*, session_id, session_cwd, is_resumed):
        seen["resolver"] = prepared.resolver
        seen["child"] = await env.child(prepared.resolver)
        seen["extra_child"] = await env.child(prepared.resolver, env.plan_extra())
        return fake_session

    prepared.create_session = real_create_session
    monkeypatch.setattr(laa, "_load_dependencies", lambda: deps)

    orch = laa.AmplifierAgentOrchestrator(host.coordinator, {})
    with caplog.at_level(logging.INFO, logger=laa.__name__):
        reply = await orch.execute(
            "hi", None, {}, {}, CapturingHooks(), coordinator=host.coordinator
        )

    assert reply == "ok"
    assert isinstance(seen["resolver"], HostCanonicalModuleResolver)
    assert await seen["child"].execute("go") == "host"
    assert await seen["extra_child"].execute("go") == "host"
    assert seen["resolver"].reroutes[env.extra].reason == HOST
    text = caplog.text
    assert "runtime identity" in text and "amplifier_core" in text


@pytest.mark.asyncio
async def test_adapter_run_turn_without_wrapper_would_fail(
    env: Env, monkeypatch: pytest.MonkeyPatch
):
    """Guard the guard: if install is disabled the same boundary is refused."""
    host = await env.host_session()
    deps, captured = make_fake_deps(reply_text="ok")
    prepared = captured["prepared"]
    prepared.resolver = env.resolver("agent")

    async def real_create_session(*, session_id, session_cwd, is_resumed):
        await env.child(prepared.resolver)
        return captured["session"]

    prepared.create_session = real_create_session
    monkeypatch.setattr(laa, "_load_dependencies", lambda: deps)
    monkeypatch.setattr(laa, "install_host_canonical_resolver", lambda *a, **k: None)
    orch = laa.AmplifierAgentOrchestrator(host.coordinator, {})
    with pytest.raises(Exception) as ei:
        await orch.execute(
            "hi", None, {}, {}, CapturingHooks(), coordinator=host.coordinator
        )
    assert "Refusing to import" in _chain(ei.value)


def test_reroutes_recorded_once_and_host_lookup_memoized(env: Env):
    class CountingHost:
        def __init__(self, inner: BundleModuleResolver) -> None:
            self.inner, self.calls = inner, 0

        def get_module_source(self, module_id: str):
            self.calls += 1
            return self.inner.get_module_source(module_id)

    host = CountingHost(env.resolver("host"))
    w = HostCanonicalModuleResolver(env.resolver("agent"), host)
    for _ in range(3):
        w.resolve(env.extra)
        w.resolve(env.only)  # host has no copy: fallback every time
    assert list(w.reroutes) == [env.extra]
    assert w.fallbacks == {env.only}
    assert host.calls == 2  # one host lookup per distinct module id


def test_get_module_source_and_passthrough(env: Env):
    agent = env.resolver("agent")
    w = HostCanonicalModuleResolver(agent, env.resolver("host"))
    host_root = str((env.root / "host" / f"amplifier-module-{env.ctx}").resolve())
    assert w.get_module_source(env.ctx) == host_root
    assert w.get_module_source(env.only) == agent.get_module_source(env.only)
    assert w.get_module_source("does-not-exist") is None
    assert not hasattr(w, "_paths")  # private state is deliberately not forwarded
    assert w.agent_resolver is agent


def test_loaded_package_without_file_is_ignored(
    env: Env, monkeypatch: pytest.MonkeyPatch
):
    """Namespace-style package (``__file__`` is None): no canonical copy."""
    name = "amplifier_module_" + env.ctx.replace("-", "_")
    monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    w = HostCanonicalModuleResolver(env.resolver("agent"))
    assert w.resolve(env.ctx).resolve() == (
        env.resolver("agent").resolve(env.ctx).resolve()
    )
    assert env.ctx in w.fallbacks


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [False, True])
async def test_hinted_identical_checkout_reuses_surviving_submodule(
    env: Env, wrapped: bool
):
    """Real Core guard, with the exact surviving-descendant shape from ACA."""
    import importlib

    body = "from . import estimate\n" + CTX.format(copy="same")
    for copy in ("host", "agent"):
        _write(env.root, copy, env.ctx, body)
        package = (
            env.root / copy / f"amplifier-module-{env.ctx}" / hcr._package_name(env.ctx)
        )
        (package / "estimate.py").write_text("VALUE = 1\n")
    await env.host_session()
    package_name = hcr._package_name(env.ctx)
    assert importlib.import_module(package_name + ".estimate")
    del sys.modules[package_name]
    plan = {
        **env.plan,
        "session": {
            **env.plan["session"],
            "context": {"module": env.ctx, "source": "explicit-same-source"},
        },
    }
    negative = BundleModuleResolver(
        {
            env.ctx: env.root / "agent" / f"amplifier-module-{env.ctx}",
            env.loop: env.root / "host" / f"amplifier-module-{env.loop}",
        }
    )
    if not wrapped:
        with pytest.raises(Exception) as ei:
            await env.child(negative, plan)
        assert "cached submodule" in _chain(ei.value)
        return
    wrapper = HostCanonicalModuleResolver(env.resolver("agent"), env.resolver("host"))
    child = await env.child(wrapper, plan)
    assert await child.execute("go") == "same"
    assert wrapper.reroutes[env.ctx].reason == LOADED


@pytest.mark.asyncio
async def test_hinted_different_loaded_checkout_is_still_refused(env: Env):
    await env.host_session()
    wrapper = HostCanonicalModuleResolver(env.resolver("agent"), env.resolver("host"))
    plan = {
        **env.plan,
        "session": {
            **env.plan["session"],
            "context": {"module": env.ctx, "source": "different-source"},
        },
    }
    with pytest.raises(Exception) as ei:
        await env.child(wrapper, plan)
    assert "Refusing to import" in _chain(ei.value)
    assert env.ctx not in wrapper.reroutes


def test_conflicting_loaded_descendant_roots_fail_closed(env: Env, monkeypatch):
    pkg = hcr._package_name(env.ctx)
    for copy in ("host", "agent"):
        mod = types.ModuleType(pkg + "." + copy)
        mod.__file__ = str(
            env.root / copy / f"amplifier-module-{env.ctx}" / pkg / "child.py"
        )
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
    wrapper = HostCanonicalModuleResolver(env.resolver("agent"), env.resolver("host"))
    with pytest.raises(RuntimeError, match="multiple roots"):
        wrapper.resolve(env.ctx)


def test_source_identity_fails_closed_on_walk_error(env: Env, monkeypatch):
    def unreadable(root, *, onerror):
        onerror(PermissionError("unreadable subtree"))
        yield  # keep the same lazy walk interface

    monkeypatch.setattr(hcr.os, "walk", unreadable)
    assert HostCanonicalModuleResolver._tree_identity(env.root) is None


def test_source_identity_bounds_empty_directories(env: Env, monkeypatch):
    def too_many(root, *, onerror):
        for _ in range(2049):
            yield str(root), [], []

    monkeypatch.setattr(hcr.os, "walk", too_many)
    assert HostCanonicalModuleResolver._tree_identity(env.root) is None
