"""Tests for ``amplifier_module_pipeline_runner.default_worker`` -- the
CLI-level default-worker resolution ladder (maintainer policy: amplifier-agent
is the bet for new dot-runner surfaces; ``direct`` is the honest fallback).

Covers:
  1. ``amplifier_agent_available()`` -- the cheap, no-import ``find_spec``
     probe (adapter present/absent x amplifier_agent_lib present/absent).
  2. ``resolve()`` -- explicit worker/bundle always wins; available ->
     synthesizes + wires the minimal bundle (spawn + profiles map read back
     exactly as an explicit ``--bundle`` would); absent -> exactly one
     stderr notice line, inputs unchanged (falls back to ``direct``
     downstream, unmodified).
  3. ``synthesize_default_agent_bundle_yaml()`` -- real
     ``amplifier_foundation.load_bundle()`` proof that the synthesized YAML
     parses and its declared worker/profiles/agents block read back exactly
     as ``runner._declared_worker_and_profiles`` expects.
  4. CLI wiring (``cmd_run``/``cmd_resume``): no explicit choice + available
     -> ``runner.run_pipeline``/``resume_pipeline`` receive
     ``worker=None, bundle=<synth path>``; no explicit choice + absent ->
     both stay ``None`` and exactly one stderr line; explicit
     ``--worker direct`` respected even when amplifier-agent is available
     (the probe is never even consulted); explicit ``--bundle`` wins too.
     Resume mirrors run exactly (consistency requirement).
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
    (non-``None``) matters to ``amplifier_agent_available()``."""


def _patch_find_spec(monkeypatch, *, adapter: bool, agent_lib: bool) -> None:
    def fake_find_spec(name):
        if name == default_worker._ADAPTER_MODULE:
            return _FakeSpec() if adapter else None
        if name == default_worker._AGENT_LIB_MODULE:
            return _FakeSpec() if agent_lib else None
        raise AssertionError(f"unexpected find_spec probe for {name!r}")

    monkeypatch.setattr(default_worker.importlib.util, "find_spec", fake_find_spec)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.delenv("DOT_RUNNER_BUNDLE", raising=False)


# ---------------------------------------------------------------------------
# 1. amplifier_agent_available() -- the probe
# ---------------------------------------------------------------------------


def test_probe_true_when_both_present(monkeypatch):
    _patch_find_spec(monkeypatch, adapter=True, agent_lib=True)
    assert default_worker.amplifier_agent_available() is True


def test_probe_false_when_adapter_absent(monkeypatch):
    _patch_find_spec(monkeypatch, adapter=False, agent_lib=True)
    assert default_worker.amplifier_agent_available() is False


def test_probe_false_when_agent_lib_absent(monkeypatch):
    """Adapter installed but its heavy peer isn't (e.g. a stale/partial
    install, or the ``[agent]`` extra was never installed) -- NOT enough to
    host a real turn."""
    _patch_find_spec(monkeypatch, adapter=True, agent_lib=False)
    assert default_worker.amplifier_agent_available() is False


def test_probe_short_circuits_before_checking_agent_lib(monkeypatch):
    """Cheap-probe proof: when the adapter itself is absent, the peer
    library is never even probed."""
    calls = []

    def fake_find_spec(name):
        calls.append(name)
        if name == default_worker._ADAPTER_MODULE:
            return None
        raise AssertionError("amplifier_agent_lib should never be probed here")

    monkeypatch.setattr(default_worker.importlib.util, "find_spec", fake_find_spec)
    assert default_worker.amplifier_agent_available() is False
    assert calls == [default_worker._ADAPTER_MODULE]


# ---------------------------------------------------------------------------
# 2 & 3. resolve() + the synthesized bundle's real shape
# ---------------------------------------------------------------------------


def test_resolve_explicit_worker_wins_even_when_available(monkeypatch, capsys):
    monkeypatch.setattr(default_worker, "amplifier_agent_available", lambda: True)
    worker, bundle = default_worker.resolve(worker="direct", bundle=None)
    assert (worker, bundle) == ("direct", None)
    assert capsys.readouterr().err == ""


def test_resolve_explicit_bundle_wins_even_when_unavailable(monkeypatch, capsys):
    monkeypatch.setattr(default_worker, "amplifier_agent_available", lambda: False)
    worker, bundle = default_worker.resolve(
        worker=None, bundle="git+https://example.invalid/x.yaml"
    )
    assert (worker, bundle) == (None, "git+https://example.invalid/x.yaml")
    assert capsys.readouterr().err == ""


def test_resolve_does_not_even_consult_the_probe_when_worker_given(monkeypatch):
    def _boom():
        raise AssertionError("probe must not run -- an explicit choice was made")

    monkeypatch.setattr(default_worker, "amplifier_agent_available", _boom)
    assert default_worker.resolve(worker="direct", bundle=None) == ("direct", None)


def test_resolve_available_synthesizes_and_wires_bundle(monkeypatch):
    monkeypatch.setattr(default_worker, "amplifier_agent_available", lambda: True)
    worker, bundle = default_worker.resolve(worker=None, bundle=None)

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


def test_resolve_absent_prints_exactly_one_notice_and_falls_back(monkeypatch, capsys):
    monkeypatch.setattr(default_worker, "amplifier_agent_available", lambda: False)
    worker, bundle = default_worker.resolve(worker=None, bundle=None, prog="dot-runner")

    assert (worker, bundle) == (None, None)
    err = capsys.readouterr().err
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) == 1
    assert default_worker.UPGRADE_HINT in lines[0]
    assert "dot-runner" in lines[0]


def test_synthesized_bundle_parses_via_real_amplifier_foundation():
    """Real proof (not a fake): ``amplifier_foundation.load_bundle()`` parses
    the synthesized YAML, and ``runner._declared_worker_and_profiles`` reads
    back exactly ``worker="spawn"`` + a profile entry per known provider --
    the SAME reader an explicit ``--bundle`` relies on."""
    amplifier_foundation = pytest.importorskip("amplifier_foundation")

    bundle_path = default_worker.write_default_agent_bundle()
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
    assert agent_entry["session"]["orchestrator"]["module"] == "loop-amplifier-agent"

    # Same regression proof as above, against the REAL parsed Bundle object
    # this time (not just a substring match on the raw YAML text).
    assert loaded.session["context"]["module"] == "context-simple"


# ---------------------------------------------------------------------------
# 4. CLI wiring: cmd_run threads the resolution through
# ---------------------------------------------------------------------------


def test_cmd_run_wires_synthesized_bundle_when_available(monkeypatch, tmp_path):
    monkeypatch.setattr(default_worker, "amplifier_agent_available", lambda: True)
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
    monkeypatch.setattr(default_worker, "amplifier_agent_available", lambda: False)
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
    monkeypatch.setattr(default_worker, "amplifier_agent_available", lambda: True)
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


def test_cmd_run_explicit_bundle_flag_wins_over_default(monkeypatch, tmp_path):
    monkeypatch.setattr(default_worker, "amplifier_agent_available", lambda: True)
    dot_path = _make_dot_file(tmp_path)

    captured: dict = {}

    async def fake_run_pipeline(dot_source, **kwargs):
        captured["bundle"] = kwargs.get("bundle")
        return PipelineResult(status="success", notes="", logs_dir=tmp_path, raw="{}")

    monkeypatch.setattr(runner_mod, "run_pipeline", fake_run_pipeline)

    parser = cli.build_parser(prog="dot-runner")
    args = parser.parse_args(
        [
            "run",
            dot_path,
            "--bundle",
            "git+https://example.invalid/explicit.yaml",
            "--cwd",
            str(tmp_path),
        ]
    )
    args.prog_name = "dot-runner"

    rc = cli.cmd_run(args)
    assert rc == 0
    assert captured["bundle"] == "git+https://example.invalid/explicit.yaml"


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
    monkeypatch.setattr(default_worker, "amplifier_agent_available", lambda: True)
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
    monkeypatch.setattr(default_worker, "amplifier_agent_available", lambda: False)
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
    monkeypatch.setattr(default_worker, "amplifier_agent_available", lambda: True)
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
