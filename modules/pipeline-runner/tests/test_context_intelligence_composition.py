"""Regression tests for dot-runner's native Context Intelligence composition.

The runner owns session composition for its public ``run_pipeline`` and
``resume_pipeline`` APIs.  Context Intelligence is therefore mounted there
once, as an ordinary hook declaration: the direct path receives it on the
runner-created session and a named worker inherits it through normal prepared
bundle composition.

These tests inspect Bundle's module-identity composition rather than emulating
it: a caller's same-module declaration must retain its source/config, and the
runner must not manufacture destinations.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from amplifier_foundation import Bundle
from amplifier_foundation.bundle._prepared import BundleModuleResolver, PreparedBundle

from amplifier_module_pipeline_runner import runner
from amplifier_module_pipeline_runner.default_worker import _HOOK_MODULE_SOURCES


def test_default_capture_includes_pipeline_event_discovery_once() -> None:
    """CI needs the existing discovery producer even on the bare direct path."""
    base = Bundle(
        name="named-base",
        hooks=[
            {
                "module": "hooks-pipeline-observability",
                "source": _HOOK_MODULE_SOURCES["hooks-pipeline-observability"],
                "config": {"caller": True},
            }
        ],
    )
    for composed in (
        runner._context_intelligence_overlay(),
        runner._context_intelligence_overlay().compose(base),
    ):
        observers = [
            hook
            for hook in composed.to_mount_plan()["hooks"]
            if hook["module"] == "hooks-pipeline-observability"
        ]
        assert len(observers) == 1
        assert (
            observers[0]["source"]
            == _HOOK_MODULE_SOURCES["hooks-pipeline-observability"]
        )
    assert observers[0]["config"] == {"caller": True}


def _ci_hook(bundle: Bundle) -> dict:
    hooks = [
        hook
        for hook in bundle.to_mount_plan().get("hooks", [])
        if hook.get("module") == "hook-context-intelligence"
    ]
    assert len(hooks) == 1, f"expected exactly one CI hook, got {hooks!r}"
    return hooks[0]


def test_default_ci_hook_declaration_is_source_pinned_and_has_no_config() -> None:
    hook = _ci_hook(runner._context_intelligence_overlay())

    assert hook == {
        "module": "hook-context-intelligence",
        "source": (
            "git+https://github.com/microsoft/amplifier-bundle-context-intelligence"
            "@3e7d597f086e8c1462ddb132976f78a5bd0d6af7"
            "#subdirectory=modules/hook-context-intelligence"
        ),
    }


def test_explicit_base_ci_source_and_config_override_the_default() -> None:
    base = Bundle(
        name="caller-base",
        hooks=[
            {
                "module": "hook-context-intelligence",
                "source": "git+https://example.invalid/caller-ci@base",
                "config": {"workspace": "caller-base"},
            },
            {
                "module": "hooks-unrelated",
                "source": "git+https://example.invalid/other",
            },
        ],
    )

    composed = runner._context_intelligence_overlay().compose(base)
    hook = _ci_hook(composed)

    assert hook["source"] == "git+https://example.invalid/caller-ci@base"
    assert hook["config"] == {"workspace": "caller-base"}
    assert {entry["module"] for entry in composed.to_mount_plan()["hooks"]} == {
        "hook-context-intelligence",
        "hooks-pipeline-observability",
        "hooks-unrelated",
    }


def test_extra_ci_overlay_overrides_default_by_module_identity() -> None:
    extra = Bundle(
        name="caller-extra",
        hooks=[
            {
                "module": "hook-context-intelligence",
                "source": "git+https://example.invalid/caller-ci@extra",
                "config": {"workspace": "caller-extra"},
            }
        ],
    )

    composed = runner._context_intelligence_overlay().compose(extra)
    hook = _ci_hook(composed)

    assert hook["source"] == "git+https://example.invalid/caller-ci@extra"
    assert hook["config"] == {"workspace": "caller-extra"}


def test_build_prepared_composes_default_hook_for_explicit_llm_direct(
    monkeypatch, tmp_path
) -> None:
    """The explicit direct path has one mounted hook before it creates a session."""

    class FakePrepared:
        pass

    class FakeBundle:
        def __init__(self, applied: list[object] | None = None) -> None:
            self.applied = applied or []

        def compose(self, other: object) -> "FakeBundle":
            return FakeBundle([*self.applied, other])

        async def prepare(self, *, install_deps: bool) -> FakePrepared:
            del install_deps
            prepared = FakePrepared()
            prepared.applied = self.applied
            return prepared

    monkeypatch.setattr(runner, "_bare_base_bundle", lambda: FakeBundle())

    prepared = asyncio.run(
        runner._build_prepared(
            "digraph T { start [shape=Mdiamond]; end [shape=Msquare]; start -> end; }",
            tmp_path,
            params=None,
            profiles=None,
            worker="llm-direct",
        )
    )

    ci_overlays = [
        bundle
        for bundle in prepared.applied
        if getattr(bundle, "hooks", None)
        and bundle.hooks[0]["module"] == "hook-context-intelligence"
    ]
    assert len(ci_overlays) == 1


def test_build_prepared_named_path_has_one_ci_hook_and_preserves_caller_overlays(
    monkeypatch, tmp_path
) -> None:
    """The named-worker parent carries one hook for normal spawn inheritance."""
    base = Bundle(
        name="named-worker-base",
        hooks=[
            {
                "module": "hook-context-intelligence",
                "source": "git+https://example.invalid/caller-ci@named",
                "config": {"workspace": "caller-named"},
            }
        ],
    )
    extra = Bundle(
        name="named-worker-extra",
        hooks=[
            {
                "module": "hook-context-intelligence",
                "source": "git+https://example.invalid/caller-ci@extra",
                "config": {"workspace": "caller-extra"},
            }
        ],
    )
    captured: dict[str, Bundle] = {}

    async def fake_prepare(self, *, install_deps: bool):
        del install_deps
        captured["bundle"] = self
        return object()

    monkeypatch.setattr(Bundle, "prepare", fake_prepare)

    asyncio.run(
        runner._build_prepared(
            "digraph T { start [shape=Mdiamond]; end [shape=Msquare]; start -> end; }",
            tmp_path,
            params=None,
            profiles=None,
            worker="spawn",
            base_bundle=base,
            extra_overlays=[extra],
        )
    )

    hook = _ci_hook(captured["bundle"])
    assert hook["source"] == "git+https://example.invalid/caller-ci@extra"
    assert hook["config"] == {"workspace": "caller-extra"}


def test_dependency_preparation_tracks_selected_composed_sources(
    monkeypatch, tmp_path
) -> None:
    """A caller-selected CI source cannot suppress the default CI source's install.

    The selected source is read from each actual composed mount plan: the
    module name alone is intentionally insufficient because normal composition
    lets the caller replace the runner's default source.
    """
    caller_source = "git+https://example.invalid/caller-ci@selected"
    caller = Bundle(
        name="caller-source",
        hooks=[
            {
                "module": "hook-context-intelligence",
                "source": caller_source,
                "config": {"workspace": "caller"},
            }
        ],
    )
    calls: list[tuple[bool, set[str]]] = []

    async def fake_prepare(self, *, install_deps: bool):
        sources = {
            entry["source"]
            for entry in self.to_mount_plan().get("hooks", [])
            if "source" in entry
        }
        calls.append((install_deps, sources))
        return object()

    monkeypatch.setattr(Bundle, "prepare", fake_prepare)
    monkeypatch.setattr(runner, "_PREPARED_SOURCE_IDENTITIES", set())

    async def prepare_sequence() -> None:
        await runner._build_prepared(
            "digraph T { start [shape=Mdiamond]; end [shape=Msquare]; start -> end; }",
            tmp_path,
            params=None,
            profiles=None,
            base_bundle=caller,
        )
        await runner._build_prepared(
            "digraph T { start [shape=Mdiamond]; end [shape=Msquare]; start -> end; }",
            tmp_path,
            params=None,
            profiles=None,
        )
        await runner._build_prepared(
            "digraph T { start [shape=Mdiamond]; end [shape=Msquare]; start -> end; }",
            tmp_path,
            params=None,
            profiles=None,
        )

    asyncio.run(prepare_sequence())

    observer_source = _HOOK_MODULE_SOURCES["hooks-pipeline-observability"]
    assert calls == [
        (True, {caller_source, observer_source}),
        (True, {runner._CONTEXT_INTELLIGENCE_HOOK_SOURCE, observer_source}),
        (False, {runner._CONTEXT_INTELLIGENCE_HOOK_SOURCE, observer_source}),
    ]


def test_dependency_preparation_retries_failed_source_install(
    monkeypatch, tmp_path
) -> None:
    """A failed install leaves its source eligible for the next preparation."""
    attempts: list[bool] = []

    async def fake_prepare(self, *, install_deps: bool):
        del self
        attempts.append(install_deps)
        if len(attempts) == 1:
            raise RuntimeError("dependency install failed")
        return object()

    monkeypatch.setattr(Bundle, "prepare", fake_prepare)
    monkeypatch.setattr(runner, "_PREPARED_SOURCE_IDENTITIES", set())

    async def prepare_twice() -> None:
        try:
            await runner._build_prepared(
                "digraph T { start [shape=Mdiamond]; end [shape=Msquare]; start -> end; }",
                tmp_path,
                params=None,
                profiles=None,
            )
        except RuntimeError:
            pass
        await runner._build_prepared(
            "digraph T { start [shape=Mdiamond]; end [shape=Msquare]; start -> end; }",
            tmp_path,
            params=None,
            profiles=None,
        )

    asyncio.run(prepare_twice())

    assert attempts == [True, True]


def test_explicit_dependency_install_overrides_do_not_poison_source_cache(
    monkeypatch, tmp_path
) -> None:
    """Explicit 0 stays disabled; later automatic and explicit 1 stay enabled."""
    calls: list[bool] = []

    async def fake_prepare(self, *, install_deps: bool):
        del self
        calls.append(install_deps)
        return object()

    monkeypatch.setattr(Bundle, "prepare", fake_prepare)
    monkeypatch.setattr(runner, "_PREPARED_SOURCE_IDENTITIES", set())
    monkeypatch.setenv("ATTRACTOR_INSTALL_DEPS", "0")

    async def prepare_sequence() -> None:
        await runner._build_prepared(
            "digraph T { start [shape=Mdiamond]; end [shape=Msquare]; start -> end; }",
            tmp_path,
            params=None,
            profiles=None,
        )
        monkeypatch.delenv("ATTRACTOR_INSTALL_DEPS")
        await runner._build_prepared(
            "digraph T { start [shape=Mdiamond]; end [shape=Msquare]; start -> end; }",
            tmp_path,
            params=None,
            profiles=None,
        )
        monkeypatch.setenv("ATTRACTOR_INSTALL_DEPS", "1")
        await runner._build_prepared(
            "digraph T { start [shape=Mdiamond]; end [shape=Msquare]; start -> end; }",
            tmp_path,
            params=None,
            profiles=None,
        )

    asyncio.run(prepare_sequence())

    assert calls == [False, True, True]


def test_public_prepared_session_lifecycle_runs_hook_ready_callback(tmp_path) -> None:
    """A normal ``create_session`` dispatches a mounted hook's ready callback.

    The local probe isolates Foundation's lifecycle dispatch from CI's runtime
    dependencies and operator configuration. The production hook identity and
    source are asserted separately above.
    """
    hook_root = tmp_path / "ci-hook"
    package = hook_root / "amplifier_module_hook_lifecycle_probe"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        """
__amplifier_module_type__ = "hook"

async def mount(coordinator, config):
    coordinator.register_capability("ci.lifecycle.probe", {"mounted": True})

async def on_session_ready(coordinator):
    coordinator.get_capability("ci.lifecycle.probe")["ready"] = True
""",
        encoding="utf-8",
    )
    context_root = tmp_path / "context"
    context_package = context_root / "amplifier_module_context_lifecycle_probe"
    context_package.mkdir(parents=True)
    (context_package / "__init__.py").write_text(
        """
__amplifier_module_type__ = "context"

class Context:
    def __init__(self):
        self.messages = []

    async def add_message(self, message):
        self.messages.append(message)

    async def get_messages_for_request(self, token_budget, provider=None):
        return self.messages.copy()

    async def get_messages(self):
        return self.messages.copy()

    async def set_messages(self, messages):
        self.messages = messages.copy()

    async def clear(self):
        self.messages.clear()

async def mount(coordinator, config):
    await coordinator.mount("context", Context())
""",
        encoding="utf-8",
    )

    bundle = Bundle(name="lifecycle-probe")
    prepared = PreparedBundle(
        bundle=bundle,
        mount_plan={
            "session": {
                "orchestrator": {"module": "loop-pipeline"},
                "context": {
                    "module": "context-lifecycle-probe",
                    "source": str(context_root),
                },
            },
            "hooks": [{"module": "hook-lifecycle-probe", "source": str(hook_root)}],
        },
        resolver=BundleModuleResolver(
            module_paths={
                "context-lifecycle-probe": Path(context_root),
                "hook-lifecycle-probe": Path(hook_root),
            }
        ),
    )

    async def create_and_close() -> dict:
        session = await prepared.create_session(session_cwd=tmp_path)
        state = session.coordinator.get_capability("ci.lifecycle.probe")
        async with session:
            pass
        return state

    assert asyncio.run(create_and_close()) == {"mounted": True, "ready": True}
