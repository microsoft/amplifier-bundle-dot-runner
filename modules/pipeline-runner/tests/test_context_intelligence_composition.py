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

    bundle = Bundle(name="lifecycle-probe")
    prepared = PreparedBundle(
        bundle=bundle,
        mount_plan={
            "session": {
                "orchestrator": {"module": "loop-pipeline"},
                "context": {"module": "context-simple"},
            },
            "hooks": [{"module": "hook-lifecycle-probe", "source": str(hook_root)}],
        },
        resolver=BundleModuleResolver(
            module_paths={"hook-lifecycle-probe": Path(hook_root)}
        ),
    )

    async def create_and_close() -> dict:
        session = await prepared.create_session(session_cwd=tmp_path)
        state = session.coordinator.get_capability("ci.lifecycle.probe")
        async with session:
            pass
        return state

    assert asyncio.run(create_and_close()) == {"mounted": True, "ready": True}
