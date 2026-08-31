"""Library-seam default-worker ladder (fix/library-seam-default-worker,
2026-08-31, maintainer ruling: "ONE behavior on both seams -- fail loud,
never silently degraded").

THE INCIDENT (proven live in a DTU, 2026-08-30): microsoft/amplifier-app-
wiki-weaver consumes this engine as a LIBRARY -- ``from
amplifier_module_pipeline_runner import run_pipeline``, called with no
``bundle=`` and no worker choice. Post-overhaul, ``run_pipeline`` hardcoded
``resolved_worker = "llm-direct"`` whenever the caller made no explicit
choice, so a spawn-path DOT graph ran silently on the TEXT-ONLY unified-llm
worker (no tool loop): the model emitted tool calls as prose, nothing
executed, 137 iterations / 16 minutes of paid LLM calls, then the step
bound -- "did not converge". The CLI (``dot-runner run``) never had this
bug: ``cli.py`` always calls ``default_worker.resolve()`` first, and
amplifier-agent is its unconditional default worker.

THE FIX: ``run_pipeline``/``resume_pipeline`` now apply the SAME ladder via
``default_worker.resolve_for_library`` (shared, not duplicated -- see
``runner._resolve_worker_and_bundle_defaults``) whenever the caller gave no
explicit ``bundle=``.

Covers (task's RED-proof discipline, items a-f):
  (a) THE WIKI-WEAVER CLASS, pinned: a "control" proving the OLD default
      shape (``llm-direct``, no spawn) is still reachable ONLY via an
      explicit, deliberate choice, paired with "the fix" proving a bare
      call no longer lands there -- the mount plan instead carries the
      amplifier-agent worker + providers.
  (b) ``worker=`` for each of the three names -- hermetic mount-plan
      assertions, no live LLM.
  (c) unknown worker (never registered) + retired names -> loud, with a
      rename hint for the retired case.
  (d) ``worker=`` + ``bundle=`` together -> ``ValueError``.
  (e) (existing coverage lives in test_dot_runner_cli.py -- explicit
      ``bundle=`` callers stay green, byte-unchanged.)
  (f) broken-install simulation -> loud error, never a text-only fallback.

All fakes mirror test_dot_runner_cli.py's established
FakeBundle/FakePrepared/FakeSession/FakeCoordinator pattern -- hermetic,
no network, no real LLM call anywhere in this file.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from amplifier_module_pipeline_runner import default_worker
from amplifier_module_pipeline_runner import runner as runner_mod

# ---------------------------------------------------------------------------
# Shared fakes (mirrors test_dot_runner_cli.py / test_engine_native_direct_
# provider.py's FakeBundle/FakePrepared/FakeSession/FakeCoordinator).
# ---------------------------------------------------------------------------


class FakePrepared:
    def __init__(self, applied: list) -> None:
        self.applied = applied
        self.bundle = type("B", (), {"agents": {}})()

    async def create_session(self, **kwargs):
        del kwargs
        return FakeSession()


class FakeSession:
    def __init__(self) -> None:
        self.coordinator = FakeCoordinator()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeCoordinator:
    def __init__(self) -> None:
        self.registered: dict[str, object] = {}
        self.config: dict = {"agents": {}}
        self.hooks = None
        self.session = None

    def register_capability(self, name: str, value: object) -> None:
        self.registered[name] = value

    def get_capability(self, name: str):
        return self.registered.get(name)


class FakeBundle:
    """Records ``.compose()`` calls; carries a controllable ``.session``."""

    def __init__(
        self, applied: list | None = None, session: dict | None = None
    ) -> None:
        self.applied = applied or []
        self.session = session or {}

    def compose(self, other):
        return FakeBundle(applied=[*self.applied, other], session=self.session)

    async def prepare(self, *, install_deps):
        del install_deps
        return FakePrepared(applied=self.applied)


def _patch_bare_base_bundle(monkeypatch) -> None:
    monkeypatch.setattr(runner_mod, "_bare_base_bundle", lambda: FakeBundle())


def _make_dot_source() -> str:
    return "digraph T { start [shape=Mdiamond]; done [shape=Msquare]; start -> done; }"


def _capture_drive_engine(monkeypatch) -> dict:
    """Fakes ``drive_engine`` and records the mount-plan kwargs it received
    -- the hermetic "what worker/profiles would this run actually use"
    assertion this whole file relies on. No LLM, no engine, no network."""
    captured: dict = {}

    async def fake_drive_engine(dot_source, coordinator, **kwargs):
        captured["coordinator"] = coordinator
        captured["profiles"] = kwargs.get("profiles")
        captured["default_worker"] = kwargs.get("default_worker")

        class _Outcome:
            class _Status:
                value = "success"

            status = _Status()
            notes = ""
            failure_reason = None

        return _Outcome()

    monkeypatch.setattr(runner_mod, "drive_engine", fake_drive_engine)
    return captured


def _patch_load_named_bundle_capturing_ref(monkeypatch) -> dict:
    """Fakes ``_load_named_bundle`` -- captures the ref it was called with
    (the REAL, on-disk synthesized bundle path when the ladder produced
    one) and returns a FakeBundle declaring `worker: spawn` + a profile per
    known provider, mirroring exactly what the real synthesized YAML
    declares (proven separately, at the ``default_worker`` layer, by
    test_default_worker.py's own real-``amplifier_foundation`` test). This
    keeps this file's tests hermetic (no real ``Bundle.prepare()``, which
    would reach the network to resolve the synthesized bundle's declared
    provider/tool modules) while still proving the REAL synthesis ran, by
    reading the captured ref's file contents directly.
    """
    from amplifier_module_pipeline_runner import provider_detection

    captured: dict = {"ref": None}

    async def fake_load_named_bundle(ref):
        captured["ref"] = ref
        return FakeBundle(
            session={
                "orchestrator": {
                    "config": {
                        "worker": "spawn",
                        "profiles": {
                            name: default_worker.DEFAULT_AGENT_NAME
                            for name in provider_detection.PROVIDER_SPECS
                        },
                    }
                }
            }
        )

    monkeypatch.setattr(runner_mod, "_load_named_bundle", fake_load_named_bundle)
    return captured


# ---------------------------------------------------------------------------
# (a) THE WIKI-WEAVER CLASS -- control (old shape, now opt-in only) + fix
# ---------------------------------------------------------------------------


def test_control_explicit_llm_direct_still_reaches_the_text_only_worker(
    monkeypatch, tmp_path
):
    """CONTROL: this is the exact mount-plan shape a bare `run_pipeline()`
    call used to produce SILENTLY (the wiki-weaver incident). It remains a
    fully legal, reachable configuration -- but only via an EXPLICIT,
    deliberate `worker="llm-direct"` choice (requirement: 'explicit
    llm-direct stays legal -- no nannying'). No bundle is ever loaded; no
    `session.spawn` capability is ever registered.
    """
    _patch_bare_base_bundle(monkeypatch)

    def _boom(name):
        raise AssertionError("the probe must never run for an explicit llm-direct ask")

    monkeypatch.setattr(default_worker, "_worker_available", _boom)

    def _forbid_load(ref):
        raise AssertionError("llm-direct must never load a bundle")

    monkeypatch.setattr(runner_mod, "_load_named_bundle", lambda ref: _forbid_load(ref))
    captured = _capture_drive_engine(monkeypatch)

    result = asyncio.run(
        runner_mod.run_pipeline(
            _make_dot_source(),
            worker="llm-direct",
            cwd=tmp_path / "work",
            logs_root=tmp_path / "logs",
        )
    )

    assert result.status == "success"
    assert captured["default_worker"] == "llm-direct"
    assert captured["profiles"] == {}
    assert "session.spawn" not in captured["coordinator"].registered


def test_fix_bare_run_pipeline_no_longer_silently_lands_on_llm_direct(
    monkeypatch, tmp_path
):
    """THE FIX, proven hermetically: a bare `run_pipeline()` call -- no
    `worker=`, no `bundle=`, exactly the wiki-weaver call shape -- with
    amplifier-agent simulated as available (`_worker_available` patched
    True, since this test environment does not have the real peer library
    installed) now carries the amplifier-agent worker + providers in its
    mount plan, NOT the text-only `llm-direct` worker.
    """
    monkeypatch.setattr(default_worker, "_worker_available", lambda name: True)
    ref_capture = _patch_load_named_bundle_capturing_ref(monkeypatch)
    captured = _capture_drive_engine(monkeypatch)

    result = asyncio.run(
        runner_mod.run_pipeline(
            _make_dot_source(),
            cwd=tmp_path / "work",
            logs_root=tmp_path / "logs",
        )
    )

    assert result.status == "success"
    # The mount plan now carries the SPAWN worker (amplifier-agent's real
    # execution mechanism), never the silent llm-direct degrade.
    assert captured["default_worker"] == "spawn"
    assert captured["default_worker"] != "llm-direct"
    assert "session.spawn" in captured["coordinator"].registered
    # Providers routed -- a spawned worker can actually reach a model.
    assert captured["profiles"]
    # Real proof the SHARED default_worker synthesis machinery ran (never
    # a second, hand-duplicated bundle): the ref handed to
    # `_load_named_bundle` is a real, on-disk YAML file containing the
    # synthesized agent name and `worker: spawn`.
    assert ref_capture["ref"] is not None
    synthesized_text = Path(ref_capture["ref"]).read_text(encoding="utf-8")
    assert default_worker.DEFAULT_AGENT_NAME in synthesized_text
    assert "worker: spawn" in synthesized_text
    assert "loop-amplifier-agent" in synthesized_text


def test_fix_resume_pipeline_applies_the_same_ladder(monkeypatch, tmp_path):
    """resume_pipeline mirrors run_pipeline's fix exactly (consistency
    requirement) -- a bare resume (no worker=, no bundle=) also resolves to
    amplifier-agent's spawn worker when available, using the checkpoint's
    OWN embedded dot_source."""
    import json

    from amplifier_module_loop_pipeline.checkpoint import (
        SCHEMA_VERSION,
        fingerprint_dot_source,
    )

    dot_source = _make_dot_source()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "checkpoint.json").write_text(
        json.dumps(
            {
                "current_node": "start",
                "completed_nodes": [],
                "context": {},
                "timestamp": "2026-08-29T00:00:00Z",
                "node_retries": {},
                "logs": [],
                "schema_version": SCHEMA_VERSION,
                "run_state": "in_flight",
                "node_outcomes": {},
                "engine_state": {
                    "iteration_count": 0,
                    "node_execution_counts": {},
                    "goal_gate_retries": 0,
                    "failure_routing_retries": 0,
                    "steps": 0,
                },
                "graph": {
                    "fingerprint": fingerprint_dot_source(dot_source),
                    "dot_source": dot_source,
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(default_worker, "_worker_available", lambda name: True)
    _patch_load_named_bundle_capturing_ref(monkeypatch)
    captured = _capture_drive_engine(monkeypatch)

    result = asyncio.run(runner_mod.resume_pipeline(run_dir, cwd=tmp_path / "work"))

    assert result.status == "success"
    assert captured["default_worker"] == "spawn"
    assert "session.spawn" in captured["coordinator"].registered


# ---------------------------------------------------------------------------
# (b) worker= for each of the three registered names -- hermetic
# ---------------------------------------------------------------------------


def test_worker_llm_direct_explicit_no_bundle_no_probe(monkeypatch, tmp_path):
    _patch_bare_base_bundle(monkeypatch)

    def _boom(name):
        raise AssertionError("llm-direct must never consult the probe")

    monkeypatch.setattr(default_worker, "_worker_available", _boom)
    captured = _capture_drive_engine(monkeypatch)

    result = asyncio.run(
        runner_mod.run_pipeline(
            _make_dot_source(),
            worker="llm-direct",
            cwd=tmp_path / "work",
            logs_root=tmp_path / "logs",
        )
    )

    assert result.status == "success"
    assert captured["default_worker"] == "llm-direct"
    assert "session.spawn" not in captured["coordinator"].registered


@pytest.mark.parametrize(
    ("worker_name", "expected_module_text"),
    [
        ("coding-agent", "module: loop-agent"),
        ("amplifier-agent", "loop-amplifier-agent"),
    ],
)
def test_worker_named_adapter_explicit_synthesizes_and_wires_bundle(
    monkeypatch, tmp_path, worker_name, expected_module_text
):
    monkeypatch.setattr(default_worker, "_worker_available", lambda name: True)
    ref_capture = _patch_load_named_bundle_capturing_ref(monkeypatch)
    captured = _capture_drive_engine(monkeypatch)

    result = asyncio.run(
        runner_mod.run_pipeline(
            _make_dot_source(),
            worker=worker_name,
            cwd=tmp_path / "work",
            logs_root=tmp_path / "logs",
        )
    )

    assert result.status == "success"
    assert captured["default_worker"] == "spawn"
    assert "session.spawn" in captured["coordinator"].registered
    synthesized_text = Path(ref_capture["ref"]).read_text(encoding="utf-8")
    assert expected_module_text in synthesized_text


# ---------------------------------------------------------------------------
# (c) unknown worker (never registered) + retired names -> loud, with a
#     rename hint for the retired case.
# ---------------------------------------------------------------------------


def test_worker_retired_name_fails_loud_with_rename_hint_no_sys_exit(
    monkeypatch, tmp_path
):
    """A retired name (`direct`/`loop-agent`) raises a normal, catchable
    exception -- NEVER `SystemExit` (that would kill the caller's host
    process) -- naming the replacement as a migration hint."""

    def _boom(name):
        raise AssertionError("probe must not run for a retired name")

    monkeypatch.setattr(default_worker, "_worker_available", _boom)

    with pytest.raises(default_worker.WorkerResolutionError) as exc_info:
        asyncio.run(
            runner_mod.run_pipeline(
                _make_dot_source(),
                worker="direct",
                cwd=tmp_path / "work",
                logs_root=tmp_path / "logs",
            )
        )

    message = str(exc_info.value)
    assert "'direct'" in message
    assert "'llm-direct'" in message


def test_worker_unrecognized_name_fails_loud_listing_registered_workers(
    monkeypatch, tmp_path
):
    """A name that is neither retired nor a registered named adapter is
    forwarded unchanged to the engine's own worker registry, which raises a
    loud `ValueError` listing every known worker -- the SAME mechanism the
    CLI relies on downstream (never re-implemented here)."""
    _patch_bare_base_bundle(monkeypatch)

    with pytest.raises(ValueError) as exc_info:
        asyncio.run(
            runner_mod.run_pipeline(
                _make_dot_source(),
                worker="not-a-real-worker",
                cwd=tmp_path / "work",
                logs_root=tmp_path / "logs",
            )
        )

    message = str(exc_info.value)
    assert "not-a-real-worker" in message
    assert "Known workers" in message


# ---------------------------------------------------------------------------
# (d) worker= + bundle= together -> ValueError
# ---------------------------------------------------------------------------


def test_worker_and_bundle_both_given_raises_value_error_run_pipeline(tmp_path):
    with pytest.raises(ValueError, match="mutually exclusive"):
        asyncio.run(
            runner_mod.run_pipeline(
                _make_dot_source(),
                worker="llm-direct",
                bundle="git+https://example.invalid/some-bundle.yaml",
                cwd=tmp_path / "work",
                logs_root=tmp_path / "logs",
            )
        )


def test_worker_and_bundle_both_given_raises_value_error_resume_pipeline(tmp_path):
    import json

    from amplifier_module_loop_pipeline.checkpoint import (
        SCHEMA_VERSION,
        fingerprint_dot_source,
    )

    dot_source = _make_dot_source()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "checkpoint.json").write_text(
        json.dumps(
            {
                "current_node": "start",
                "completed_nodes": [],
                "context": {},
                "timestamp": "2026-08-29T00:00:00Z",
                "node_retries": {},
                "logs": [],
                "schema_version": SCHEMA_VERSION,
                "run_state": "in_flight",
                "node_outcomes": {},
                "engine_state": {
                    "iteration_count": 0,
                    "node_execution_counts": {},
                    "goal_gate_retries": 0,
                    "failure_routing_retries": 0,
                    "steps": 0,
                },
                "graph": {
                    "fingerprint": fingerprint_dot_source(dot_source),
                    "dot_source": dot_source,
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="mutually exclusive"):
        asyncio.run(
            runner_mod.resume_pipeline(
                run_dir,
                worker="llm-direct",
                bundle="git+https://example.invalid/some-bundle.yaml",
                cwd=tmp_path / "work",
            )
        )


# ---------------------------------------------------------------------------
# (f) broken-install simulation -> loud error, never a text-only fallback
# ---------------------------------------------------------------------------


def test_broken_install_no_explicit_worker_fails_loud_never_text_only_fallback(
    monkeypatch, tmp_path
):
    """The exact wiki-weaver-adjacent danger case: amplifier-agent's
    runtime import guard trips (broken/partial install) on a BARE call.
    Must raise -- NEVER silently run the text-only worker instead."""
    monkeypatch.setattr(default_worker, "_worker_available", lambda name: False)

    async def _forbid_drive_engine(*a, **k):
        raise AssertionError(
            "drive_engine must never be reached on a broken-install fail-loud exit"
        )

    monkeypatch.setattr(runner_mod, "drive_engine", _forbid_drive_engine)

    with pytest.raises(default_worker.WorkerResolutionError) as exc_info:
        asyncio.run(
            runner_mod.run_pipeline(
                _make_dot_source(),
                cwd=tmp_path / "work",
                logs_root=tmp_path / "logs",
            )
        )

    assert default_worker.BROKEN_INSTALL_HINT in str(exc_info.value)


def test_broken_install_explicit_amplifier_agent_fails_loud(monkeypatch, tmp_path):
    monkeypatch.setattr(default_worker, "_worker_available", lambda name: False)

    async def _forbid_drive_engine(*a, **k):
        raise AssertionError("drive_engine must never be reached")

    monkeypatch.setattr(runner_mod, "drive_engine", _forbid_drive_engine)

    with pytest.raises(default_worker.WorkerResolutionError) as exc_info:
        asyncio.run(
            runner_mod.run_pipeline(
                _make_dot_source(),
                worker="amplifier-agent",
                cwd=tmp_path / "work",
                logs_root=tmp_path / "logs",
            )
        )

    assert default_worker.BROKEN_INSTALL_HINT in str(exc_info.value)


def test_broken_install_never_raises_sys_exit_on_the_library_seam(
    monkeypatch, tmp_path
):
    """Regression guard: the library seam must NEVER call `sys.exit` (that
    would kill the caller's host process, e.g. wiki-weaver's) -- only the
    CLI seam (`default_worker.resolve`) does that."""
    monkeypatch.setattr(default_worker, "_worker_available", lambda name: False)

    try:
        asyncio.run(
            runner_mod.run_pipeline(
                _make_dot_source(),
                cwd=tmp_path / "work",
                logs_root=tmp_path / "logs",
            )
        )
    except SystemExit:
        pytest.fail("run_pipeline must never raise SystemExit on the library seam")
    except default_worker.WorkerResolutionError:
        pass  # expected -- a normal, catchable exception
