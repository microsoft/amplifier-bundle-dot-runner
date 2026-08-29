"""Tests for ``amplifier_module_pipeline_runner.default_worker`` -- named
worker resolution (maintainer policy, WAVE 5 repair 2026-08-30: worker
NAMES -- ``direct`` | ``loop-agent`` | ``amplifier-agent`` -- are the whole
user-facing concept; ``--bundle``/``DOT_RUNNER_BUNDLE`` are removed from the
CLI surface entirely. Any bundle machinery a named worker needs is
synthesized internally by this module and never surfaced to the user).

Covers:
  1. ``_worker_available()`` / its back-compat ``amplifier_agent_available()``
     alias -- the cheap, no-import ``find_spec`` probe (adapter present/
     absent x its probe module present/absent), for BOTH registered names.
  2. ``resolve()`` -- explicit ``worker="direct"`` always wins with zero
     probing; an explicit named worker (``loop-agent``/``amplifier-agent``)
     synthesizes + wires its own minimal bundle when available, fails loud
     (``SystemExit(1)``) when not; an unrecognized name is returned
     unchanged so the registry's own "Unknown worker" error fires
     downstream; no explicit choice (``worker=None``) attempts the
     amplifier-agent default bet, falling back to ``direct`` with exactly
     one stderr notice when unavailable.
  3. ``_synthesize_agent_bundle_yaml()`` -- real
     ``amplifier_foundation.load_bundle()`` proof that the synthesized YAML
     parses and its declared worker/profiles/agent-orchestrator-module read
     back exactly as ``runner._declared_worker_and_profiles`` (and the spawn
     machinery) expect -- for BOTH registered worker names, including the
     regression proof that a worker's NAME (e.g. ``amplifier-agent``) is
     never confused with its adapter's REGISTERED module name (e.g.
     ``loop-amplifier-agent``, per that module's own
     ``[project.entry-points."amplifier.modules"]`` table).
  4. CLI wiring (``cmd_run``/``cmd_resume``): no explicit choice + available
     -> ``runner.run_pipeline``/``resume_pipeline`` receive
     ``worker=None, bundle=<synth path>``; no explicit choice + absent ->
     both stay ``None`` and exactly one stderr line; explicit
     ``--worker direct`` respected even when amplifier-agent is available
     (the probe is never even consulted). Resume mirrors run exactly
     (consistency requirement).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from amplifier_module_pipeline_runner import cli
from amplifier_module_pipeline_runner import default_worker
from amplifier_module_pipeline_runner import runner as runner_mod
from amplifier_module_pipeline_runner.runner import PipelineResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dot_file(tmp_path) -> str:
    dot_file = tmp_path / "pipeline.dot"
    dot_file.write_text(
        "digraph T { start [shape=Mdiamond]; done [shape=Msquare]; start -> done; }",
        encoding="utf-8",
    )
    return str(dot_file)


class _FakeSpec:
    """Stand-in for ``importlib.machinery.ModuleSpec`` -- only truthiness
    (non-``None``) matters to ``_worker_available()``."""


def _patch_find_spec(
    monkeypatch, worker_name: str, *, adapter: bool, probe: bool
) -> None:
    adapter_module, probe_module, _source, _orch_module = (
        default_worker._ADAPTER_REGISTRY[worker_name]
    )

    def fake_find_spec(name):
        if name == adapter_module:
            return _FakeSpec() if adapter else None
        if name == probe_module:
            return _FakeSpec() if probe else None
        raise AssertionError(f"unexpected find_spec probe for {name!r}")

    monkeypatch.setattr(default_worker.importlib.util, "find_spec", fake_find_spec)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.delenv("DOT_RUNNER_BUNDLE", raising=False)


# ---------------------------------------------------------------------------
# 1. _worker_available() / amplifier_agent_available() -- the probe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("worker_name", ["loop-agent", "amplifier-agent"])
def test_probe_true_when_both_present(monkeypatch, worker_name):
    _patch_find_spec(monkeypatch, worker_name, adapter=True, probe=True)
    assert default_worker._worker_available(worker_name) is True


@pytest.mark.parametrize("worker_name", ["loop-agent", "amplifier-agent"])
def test_probe_false_when_adapter_absent(monkeypatch, worker_name):
    _patch_find_spec(monkeypatch, worker_name, adapter=False, probe=True)
    assert default_worker._worker_available(worker_name) is False


def test_probe_false_when_agent_lib_absent(monkeypatch):
    """amplifier-agent's adapter installed but its heavy peer isn't (e.g. a
    stale/partial install, or the ``[agent]`` extra was never installed) --
    NOT enough to host a real turn."""
    _patch_find_spec(monkeypatch, "amplifier-agent", adapter=True, probe=False)
    assert default_worker._worker_available("amplifier-agent") is False


def test_probe_true_when_adapter_doubles_as_its_own_probe(monkeypatch):
    """loop-agent has no heavy peer library distinct from its adapter -- the
    adapter module doubles as its own probe (registry: probe_module ==
    adapter_module), so adapter-present alone is sufficient."""
    _patch_find_spec(monkeypatch, "loop-agent", adapter=True, probe=True)
    assert default_worker._worker_available("loop-agent") is True


def test_probe_short_circuits_before_checking_agent_lib(monkeypatch):
    """Cheap-probe proof: when the adapter itself is absent, the peer
    library is never even probed."""
    calls = []

    def fake_find_spec(name):
        calls.append(name)
        if name == "amplifier_module_loop_amplifier_agent":
            return None
        raise AssertionError("amplifier_agent_lib should never be probed here")

    monkeypatch.setattr(default_worker.importlib.util, "find_spec", fake_find_spec)
    assert default_worker._worker_available("amplifier-agent") is False
    assert calls == ["amplifier_module_loop_amplifier_agent"]


def test_probe_unknown_name_is_false_without_any_find_spec_call(monkeypatch):
    def _boom(name):
        raise AssertionError("find_spec must never run for an unregistered name")

    monkeypatch.setattr(default_worker.importlib.util, "find_spec", _boom)
    assert default_worker._worker_available("not-a-real-worker") is False


def test_amplifier_agent_available_is_the_worker_available_alias(monkeypatch):
    """Back-compat alias: ``amplifier_agent_available()`` == ``_worker_available("amplifier-agent")``."""
    _patch_find_spec(monkeypatch, "amplifier-agent", adapter=True, probe=True)
    assert default_worker.amplifier_agent_available() is True
    _patch_find_spec(monkeypatch, "amplifier-agent", adapter=False, probe=True)
    assert default_worker.amplifier_agent_available() is False


# ---------------------------------------------------------------------------
# 2. resolve()
# ---------------------------------------------------------------------------


def test_resolve_explicit_direct_wins_even_when_available(monkeypatch, capsys):
    monkeypatch.setattr(default_worker, "_worker_available", lambda name: True)
    worker, bundle = default_worker.resolve(worker="direct")
    assert (worker, bundle) == ("direct", None)
    assert capsys.readouterr().err == ""


def test_resolve_does_not_even_consult_the_probe_when_direct_given(monkeypatch):
    def _boom(name):
        raise AssertionError("probe must not run -- an explicit choice was made")

    monkeypatch.setattr(default_worker, "_worker_available", _boom)
    assert default_worker.resolve(worker="direct") == ("direct", None)


@pytest.mark.parametrize("worker_name", ["loop-agent", "amplifier-agent"])
def test_resolve_explicit_named_worker_available_synthesizes_and_wires_bundle(
    monkeypatch, worker_name
):
    monkeypatch.setattr(default_worker, "_worker_available", lambda name: True)
    worker, bundle = default_worker.resolve(worker=worker_name)

    assert worker is None  # the SYNTHESIZED bundle's own declared worker drives it
    assert bundle is not None
    bundle_path = Path(bundle)
    assert bundle_path.is_file()
    text = bundle_path.read_text(encoding="utf-8")
    assert "worker: spawn" in text
    assert default_worker.DEFAULT_AGENT_NAME in text
    # Regression proof: this bundle is composed as the run's BASE bundle
    # (replacing runner._bare_base_bundle() outright, not merged alongside
    # it), so it must supply its own session.context -- a real end-to-end
    # run without this failed with "Configuration must specify
    # session.context" (AmplifierSession construction requirement).
    assert "context-simple" in text


def test_resolve_explicit_named_worker_unavailable_fails_loud(monkeypatch, capsys):
    monkeypatch.setattr(default_worker, "_worker_available", lambda name: False)
    with pytest.raises(SystemExit) as exc_info:
        default_worker.resolve(worker="loop-agent", prog="dot-runner")
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "loop-agent" in err
    assert "not" in err and "installed" in err
    # Named-but-uninstalled: an explicit ask fails loud rather than
    # silently degrading -- it does NOT fall back to direct on its own,
    # but it DOES suggest the alternatives.
    assert "--worker direct" in err
    assert "amplifier-agent" in err


def test_resolve_explicit_amplifier_agent_unavailable_does_not_suggest_itself(
    monkeypatch, capsys
):
    """The failing worker itself is of course NAMED in the "was requested"
    complaint -- but the suggested-alternatives tail must not loop back and
    recommend retrying the exact worker that just failed."""
    monkeypatch.setattr(default_worker, "_worker_available", lambda name: False)
    with pytest.raises(SystemExit):
        default_worker.resolve(worker="amplifier-agent", prog="dot-runner")
    err = capsys.readouterr().err
    assert "--worker direct" in err
    suggestion_tail = err.split("--worker direct", 1)[1]
    assert "amplifier-agent" not in suggestion_tail, (
        f"must not suggest retrying the worker that just failed, got tail={suggestion_tail!r}"
    )


def test_resolve_unknown_name_returned_unchanged_no_probe_consulted(monkeypatch):
    def _boom(name):
        raise AssertionError("probe must not run for an unrecognized name")

    monkeypatch.setattr(default_worker, "_worker_available", _boom)
    assert default_worker.resolve(worker="not-a-real-worker") == (
        "not-a-real-worker",
        None,
    )


def test_resolve_no_choice_available_synthesizes_amplifier_agent_bundle(monkeypatch):
    calls = []
    monkeypatch.setattr(
        default_worker,
        "_worker_available",
        lambda name: calls.append(name) or name == "amplifier-agent",
    )
    worker, bundle = default_worker.resolve(worker=None)

    assert calls == ["amplifier-agent"]  # only the default bet is probed
    assert worker is None
    assert bundle is not None
    text = Path(bundle).read_text(encoding="utf-8")
    assert "loop-amplifier-agent" in text  # the ADAPTER's real module name


def test_resolve_no_choice_absent_prints_exactly_one_notice_and_falls_back(
    monkeypatch, capsys
):
    monkeypatch.setattr(default_worker, "_worker_available", lambda name: False)
    worker, bundle = default_worker.resolve(worker=None, prog="dot-runner")

    assert (worker, bundle) == (None, None)
    err = capsys.readouterr().err
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) == 1
    assert default_worker.UPGRADE_HINT in lines[0]
    assert "dot-runner" in lines[0]


def test_upgrade_hint_does_not_teach_the_broken_single_command_install():
    """Review finding, fixed: a single `uv tool install
    "amplifier-dot-runner[agent]"` can hit a real, disclosed `uv`
    dependency-resolution collision (README's "The [agent] extra" section,
    "conflicting URLs for package amplifier-foundation"). Teaching that
    exact command as THE fix would hand a reader a command known to fail.
    The notice must instead point at a path proven to work today -- the
    README's two-step install -- not repeat the broken one-liner as the
    recommended fix."""
    hint = default_worker.UPGRADE_HINT
    assert "README" in hint
    assert "two-step" in hint
    assert not hint.strip().startswith("uv tool install")


# ---------------------------------------------------------------------------
# 3. _synthesize_agent_bundle_yaml() -- the synthesized bundle's real shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("worker_name", "expected_orch_module"),
    [
        ("loop-agent", "loop-agent"),
        ("amplifier-agent", "loop-amplifier-agent"),
    ],
)
def test_synthesized_bundle_parses_via_real_amplifier_foundation(
    worker_name, expected_orch_module
):
    """Real proof (not a fake): ``amplifier_foundation.load_bundle()`` parses
    the synthesized YAML, and ``runner._declared_worker_and_profiles`` reads
    back exactly ``worker="spawn"`` + a profile entry per known provider --
    the SAME reader an explicit bundle relies on. Also the load-bearing
    regression proof: the agent's orchestrator ``module:`` is the adapter's
    REGISTERED entry-point name, never the (possibly different) worker
    name the user typed -- ``amplifier-agent`` the worker name is hosted by
    the ``loop-amplifier-agent`` module; conflating the two would declare a
    nonexistent module and fail to mount at spawn time.
    """
    amplifier_foundation = pytest.importorskip("amplifier_foundation")

    bundle_path = default_worker.write_agent_bundle(worker_name)
    loaded = asyncio.run(amplifier_foundation.load_bundle(str(bundle_path)))

    declared_worker, declared_profiles = runner_mod._declared_worker_and_profiles(
        loaded
    )
    assert declared_worker == "spawn"
    assert declared_profiles == {
        provider: default_worker.DEFAULT_AGENT_NAME
        for provider in runner_mod.PROVIDER_KEY_ENV
    }

    agent_entry = loaded.agents[default_worker.DEFAULT_AGENT_NAME]
    assert agent_entry["session"]["orchestrator"]["module"] == expected_orch_module

    # Same regression proof as above, against the REAL parsed Bundle object
    # this time (not just a substring match on the raw YAML text).
    assert loaded.session["context"]["module"] == "context-simple"


def test_back_compat_aliases_target_amplifier_agent():
    assert (
        default_worker.synthesize_default_agent_bundle_yaml()
        == default_worker._synthesize_agent_bundle_yaml(
            default_worker.AMPLIFIER_AGENT_NAME
        )
    )
    default_path = default_worker.write_default_agent_bundle()
    assert "loop-amplifier-agent" in default_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. CLI wiring: cmd_run / cmd_resume thread the resolution through
# ---------------------------------------------------------------------------


def test_cmd_run_wires_synthesized_bundle_when_available(monkeypatch, tmp_path):
    monkeypatch.setattr(default_worker, "_worker_available", lambda name: True)
    dot_path = _make_dot_file(tmp_path)

    captured: dict = {}

    async def fake_run_pipeline(dot_source, **kwargs):
        captured["worker"] = kwargs.get("worker")
        captured["bundle"] = kwargs.get("bundle")
        return PipelineResult(status="success", notes="", logs_dir=tmp_path, raw="{}")

    monkeypatch.setattr(runner_mod, "run_pipeline", fake_run_pipeline)

    parser = cli.build_parser(prog="dot-runner")
    args = parser.parse_args(["run", dot_path, "--cwd", str(tmp_path)])
    args.prog_name = "dot-runner"

    rc = cli.cmd_run(args)
    assert rc == 0
    assert captured["worker"] is None
    assert captured["bundle"] is not None
    assert default_worker.DEFAULT_AGENT_NAME in Path(captured["bundle"]).read_text()


def test_cmd_run_falls_back_to_direct_with_one_notice_when_unavailable(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(default_worker, "_worker_available", lambda name: False)
    dot_path = _make_dot_file(tmp_path)

    captured: dict = {}

    async def fake_run_pipeline(dot_source, **kwargs):
        captured["worker"] = kwargs.get("worker")
        captured["bundle"] = kwargs.get("bundle")
        return PipelineResult(status="success", notes="", logs_dir=tmp_path, raw="{}")

    monkeypatch.setattr(runner_mod, "run_pipeline", fake_run_pipeline)

    parser = cli.build_parser(prog="dot-runner")
    args = parser.parse_args(["run", dot_path, "--cwd", str(tmp_path)])
    args.prog_name = "dot-runner"

    rc = cli.cmd_run(args)
    assert rc == 0
    assert captured["worker"] is None
    assert captured["bundle"] is None

    err = capsys.readouterr().err
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) == 1
    assert default_worker.UPGRADE_HINT in lines[0]


def test_cmd_run_explicit_worker_direct_respected_even_when_available(
    monkeypatch, tmp_path, capsys
):
    """The team's bet never overrides an explicit --worker."""
    monkeypatch.setattr(default_worker, "_worker_available", lambda name: True)
    dot_path = _make_dot_file(tmp_path)

    captured: dict = {}

    async def fake_run_pipeline(dot_source, **kwargs):
        captured["worker"] = kwargs.get("worker")
        captured["bundle"] = kwargs.get("bundle")
        return PipelineResult(status="success", notes="", logs_dir=tmp_path, raw="{}")

    monkeypatch.setattr(runner_mod, "run_pipeline", fake_run_pipeline)

    parser = cli.build_parser(prog="dot-runner")
    args = parser.parse_args(
        ["run", dot_path, "--worker", "direct", "--cwd", str(tmp_path)]
    )
    args.prog_name = "dot-runner"

    rc = cli.cmd_run(args)
    assert rc == 0
    assert captured["worker"] == "direct"
    assert captured["bundle"] is None
    assert capsys.readouterr().err == ""


def test_cmd_run_explicit_worker_loop_agent_wires_its_own_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(default_worker, "_worker_available", lambda name: True)
    dot_path = _make_dot_file(tmp_path)

    captured: dict = {}

    async def fake_run_pipeline(dot_source, **kwargs):
        captured["worker"] = kwargs.get("worker")
        captured["bundle"] = kwargs.get("bundle")
        return PipelineResult(status="success", notes="", logs_dir=tmp_path, raw="{}")

    monkeypatch.setattr(runner_mod, "run_pipeline", fake_run_pipeline)

    parser = cli.build_parser(prog="dot-runner")
    args = parser.parse_args(
        ["run", dot_path, "--worker", "loop-agent", "--cwd", str(tmp_path)]
    )
    args.prog_name = "dot-runner"

    rc = cli.cmd_run(args)
    assert rc == 0
    assert captured["worker"] is None
    text = Path(captured["bundle"]).read_text(encoding="utf-8")
    assert "module: loop-agent" in text


# ---------------------------------------------------------------------------
# Resume path consistency
# ---------------------------------------------------------------------------


def _checkpoint_payload(dot_source: str) -> dict:
    from amplifier_module_loop_pipeline.checkpoint import (
        SCHEMA_VERSION,
        fingerprint_dot_source,
    )

    return {
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


def _resume_run_dir(tmp_path):
    dot_source = (
        "digraph T { start [shape=Mdiamond]; done [shape=Msquare]; start -> done; }"
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "checkpoint.json").write_text(
        json.dumps(_checkpoint_payload(dot_source)), encoding="utf-8"
    )
    return run_dir


def test_cmd_resume_wires_synthesized_bundle_when_available(monkeypatch, tmp_path):
    monkeypatch.setattr(default_worker, "_worker_available", lambda name: True)
    run_dir = _resume_run_dir(tmp_path)

    captured: dict = {}

    async def fake_resume_pipeline(run_dir_arg, **kwargs):
        captured["worker"] = kwargs.get("worker")
        captured["bundle"] = kwargs.get("bundle")
        return PipelineResult(status="success", notes="", logs_dir=run_dir, raw="{}")

    monkeypatch.setattr(runner_mod, "resume_pipeline", fake_resume_pipeline)

    parser = cli.build_parser(prog="dot-runner")
    args = parser.parse_args(["resume", str(run_dir)])
    args.prog_name = "dot-runner"

    rc = cli.cmd_resume(args)
    assert rc == 0
    assert captured["worker"] is None
    assert captured["bundle"] is not None
    assert default_worker.DEFAULT_AGENT_NAME in Path(captured["bundle"]).read_text()


def test_cmd_resume_falls_back_to_direct_with_one_notice_when_unavailable(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(default_worker, "_worker_available", lambda name: False)
    run_dir = _resume_run_dir(tmp_path)

    captured: dict = {}

    async def fake_resume_pipeline(run_dir_arg, **kwargs):
        captured["worker"] = kwargs.get("worker")
        captured["bundle"] = kwargs.get("bundle")
        return PipelineResult(status="success", notes="", logs_dir=run_dir, raw="{}")

    monkeypatch.setattr(runner_mod, "resume_pipeline", fake_resume_pipeline)

    parser = cli.build_parser(prog="dot-runner")
    args = parser.parse_args(["resume", str(run_dir)])
    args.prog_name = "dot-runner"

    rc = cli.cmd_resume(args)
    assert rc == 0
    assert captured["worker"] is None
    assert captured["bundle"] is None

    err = capsys.readouterr().err
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) == 1
    assert default_worker.UPGRADE_HINT in lines[0]


def test_cmd_resume_explicit_worker_direct_respected_even_when_available(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(default_worker, "_worker_available", lambda name: True)
    run_dir = _resume_run_dir(tmp_path)

    captured: dict = {}

    async def fake_resume_pipeline(run_dir_arg, **kwargs):
        captured["worker"] = kwargs.get("worker")
        captured["bundle"] = kwargs.get("bundle")
        return PipelineResult(status="success", notes="", logs_dir=run_dir, raw="{}")

    monkeypatch.setattr(runner_mod, "resume_pipeline", fake_resume_pipeline)

    parser = cli.build_parser(prog="dot-runner")
    args = parser.parse_args(["resume", str(run_dir), "--worker", "direct"])
    args.prog_name = "dot-runner"

    rc = cli.cmd_resume(args)
    assert rc == 0
    assert captured["worker"] == "direct"
    assert captured["bundle"] is None
