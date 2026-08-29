"""Tests for the P3 two-CLI layering: `dot-runner` (engine-native) vs.
`attractor` (legacy, opinionated) -- DESIGN-worker-registry-core-split.md P3.

Covers (per the P3 acceptance bar):
  1. `dot-runner run` on a minimal box-node .dot with a FAKE provider
     (hermetic): executes via the `direct` worker, zero attractor-bundle/
     profile resolution attempted.
  2. `--worker` flag: selects a registered worker; unknown name -> clean
     error listing registered workers (exit code + message, no traceback).
  3. Legacy `attractor` entry point: the notice appears on stderr exactly
     once, and stdout is unaffected (byte-identical shape) for a
     representative command.
  4. Entry-point registration: both `main`/`main_dot_runner` dispatch
     correctly and `--help` works for both.
  5. doctor/lint/trace smoke under the `dot-runner` personality.

These use fakes and monkeypatching (no real bundle loading, no engine, no
LLM) so they stay fast and non-brittle, per the module's testing philosophy
(see test_extra_overlays.py / test_cli_on_human_gate.py for the established
pattern this file follows).
"""

from __future__ import annotations

import asyncio

import pytest
from amplifier_module_pipeline_runner import cli
from amplifier_module_pipeline_runner import runner as runner_mod
from amplifier_module_pipeline_runner.runner import PipelineResult

# ---------------------------------------------------------------------------
# Shared fakes (mirrors test_extra_overlays.py's FakeBundle/FakePrepared)
# ---------------------------------------------------------------------------


class FakePrepared:
    """Minimal stand-in for a PreparedBundle -- records what it was composed from."""

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
    """Records ``.compose()`` calls in order (see test_extra_overlays.py)."""

    def __init__(self, applied: list | None = None) -> None:
        self.applied = applied or []

    def compose(self, other):
        return FakeBundle(applied=[*self.applied, other])

    async def prepare(self, *, install_deps):
        del install_deps
        return FakePrepared(applied=self.applied)


def _patch_engine_native_base_bundle(monkeypatch) -> None:
    monkeypatch.setattr(runner_mod, "_engine_native_base_bundle", lambda: FakeBundle())


def _forbid_attractor_bundle_load(monkeypatch) -> list[str]:
    """Monkeypatch ``_load_base_bundle`` to record/forbid any call.

    Returns the call-count list (mutated in place) so a test can assert
    ``calls == []`` -- the hermetic proof that the attractor pattern-repo
    bundle loader (the network-reaching, gap-table-row-23 function) was
    never reached.
    """
    calls: list[str] = []

    async def spy(*_a, **_k):
        calls.append("called")
        raise AssertionError(
            "engine_native must never call _load_base_bundle -- that is the "
            "attractor-bundle network-reach path DESIGN-worker-registry-"
            "core-split.md P3 names as one of the two verified layering "
            "inversion sites."
        )

    monkeypatch.setattr(runner_mod, "_load_base_bundle", spy)
    return calls


def _make_dot_file(tmp_path, *, llm_model: str = "fake-model") -> str:
    dot_file = tmp_path / "pipeline.dot"
    dot_file.write_text(
        "digraph T { start [shape=Mdiamond]; "
        f'work [shape=box, llm_provider="anthropic", llm_model="{llm_model}", '
        'prompt="do the thing"]; '
        "done [shape=Msquare]; start -> work -> done; }",
        encoding="utf-8",
    )
    return str(dot_file)


# ---------------------------------------------------------------------------
# 1. dot-runner never reaches the attractor pattern-repo bundle loader
# ---------------------------------------------------------------------------


def test_build_prepared_engine_native_never_loads_attractor_bundle(
    monkeypatch, tmp_path
):
    """The structural proof: `_build_prepared(engine_native=True)` never
    calls `_load_base_bundle` (row 23's network-reaching function) -- it
    calls `_engine_native_base_bundle()` instead."""
    calls = _forbid_attractor_bundle_load(monkeypatch)
    _patch_engine_native_base_bundle(monkeypatch)

    prepared = asyncio.run(
        runner_mod._build_prepared(
            "digraph { start [shape=box]; }",
            tmp_path,
            params=None,
            profiles=None,
            worker="direct",
            engine_native=True,
        )
    )

    assert calls == []
    assert prepared is not None


def test_build_prepared_attractor_personality_still_uses_load_base_bundle(
    monkeypatch, tmp_path
):
    """Regression guard: the LEGACY (default, engine_native=False) path is
    UNCHANGED -- it still calls `_load_base_bundle`, never the engine-native
    bare bundle."""
    used: list[str] = []

    async def fake_load_base_bundle():
        used.append("load_base_bundle")
        return FakeBundle()

    def fail_engine_native():
        raise AssertionError(
            "attractor personality must not use the bare engine bundle"
        )

    monkeypatch.setattr(runner_mod, "_load_base_bundle", fake_load_base_bundle)
    monkeypatch.setattr(runner_mod, "_engine_native_base_bundle", fail_engine_native)

    asyncio.run(
        runner_mod._build_prepared(
            "digraph { start [shape=box]; }",
            tmp_path,
            params=None,
            profiles=None,
        )
    )
    assert used == ["load_base_bundle"]


def test_run_pipeline_engine_native_registers_no_spawn_capability(
    monkeypatch, tmp_path
):
    """`run_pipeline(engine_native=True)` never registers `session.spawn` --
    the mechanism that makes the `direct` worker the ONLY reachable path
    (no attractor-agent profile resolution attempted)."""
    _patch_engine_native_base_bundle(monkeypatch)
    calls = _forbid_attractor_bundle_load(monkeypatch)

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

    result = asyncio.run(
        runner_mod.run_pipeline(
            "digraph T { start [shape=Mdiamond]; done [shape=Msquare]; start -> done; }",
            cwd=tmp_path / "work",
            logs_root=tmp_path / "logs",
            engine_native=True,
        )
    )

    assert result.status == "success"
    assert calls == []
    # No session.spawn ever registered -- direct worker is the only path.
    assert "session.spawn" not in captured["coordinator"].registered
    # Zero implicit attractor-agent profile fallback.
    assert captured["profiles"] == {}
    # Engine-native default worker resolves to "direct" when unspecified.
    assert captured["default_worker"] == "direct"


def test_run_pipeline_attractor_personality_still_registers_spawn(
    monkeypatch, tmp_path
):
    """Regression guard: the legacy path is unaffected -- session.spawn IS
    still registered, and DEFAULT_PROFILES IS still the implicit fallback."""

    async def fake_load_base_bundle():
        return FakeBundle()

    monkeypatch.setattr(runner_mod, "_load_base_bundle", fake_load_base_bundle)

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

    asyncio.run(
        runner_mod.run_pipeline(
            "digraph T { start [shape=Mdiamond]; done [shape=Msquare]; start -> done; }",
            cwd=tmp_path / "work",
            logs_root=tmp_path / "logs",
        )
    )

    assert "session.spawn" in captured["coordinator"].registered
    assert captured["profiles"] == runner_mod.DEFAULT_PROFILES
    assert captured["default_worker"] is None


# ---------------------------------------------------------------------------
# 2. --worker flag: registered selection + clean unknown-name error
# ---------------------------------------------------------------------------


def test_worker_flag_accepted_by_argparse_for_both_personalities():
    for prog in ("attractor", "dot-runner"):
        parser = cli.build_parser(prog=prog)
        args = parser.parse_args(["run", "dummy.dot", "--worker", "direct"])
        assert args.worker == "direct"
        args2 = parser.parse_args(["resume", "some-dir", "--worker", "direct"])
        assert args2.worker == "direct"


def test_worker_flag_defaults_to_none():
    parser = cli.build_parser(prog="dot-runner")
    args = parser.parse_args(["run", "dummy.dot"])
    assert args.worker is None


def test_worker_flag_reaches_run_pipeline(monkeypatch, tmp_path):
    """cmd_run threads --worker straight through to runner.run_pipeline."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    dot_path = _make_dot_file(tmp_path)

    captured: dict = {}

    async def fake_run_pipeline(dot_source, **kwargs):
        captured["worker"] = kwargs.get("worker")
        captured["engine_native"] = kwargs.get("engine_native")
        return PipelineResult(status="success", notes="", logs_dir=tmp_path, raw="{}")

    monkeypatch.setattr(runner_mod, "run_pipeline", fake_run_pipeline)

    parser = cli.build_parser(prog="dot-runner")
    args = parser.parse_args(
        ["run", dot_path, "--worker", "direct", "--cwd", str(tmp_path)]
    )
    args.prog_name = "dot-runner"
    args.engine_native = True

    rc = cli.cmd_run(args)
    assert rc == 0
    assert captured["worker"] == "direct"
    assert captured["engine_native"] is True


def test_unknown_worker_name_fails_clean_not_a_traceback(monkeypatch, tmp_path, capsys):
    """Registry-level unknown-name refusal (AmplifierBackend's own ValueError,
    raised via the real registry construction) surfaces as a clean CLI
    error -- exit code 1, message naming the bogus name, no traceback."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    dot_path = _make_dot_file(tmp_path)

    parser = cli.build_parser(prog="dot-runner")
    args = parser.parse_args(
        ["run", dot_path, "--worker", "totally-bogus-worker", "--cwd", str(tmp_path)]
    )
    args.prog_name = "dot-runner"
    args.engine_native = True

    rc = cli.cmd_run(args)

    assert rc == 1
    err = capsys.readouterr().err
    assert "totally-bogus-worker" in err
    assert "Traceback" not in err
    assert "pipeline execution failed" in err


# ---------------------------------------------------------------------------
# 3. Legacy `attractor`: notice on stderr exactly once; stdout unaffected
# ---------------------------------------------------------------------------


def test_attractor_notice_printed_exactly_once_on_stderr(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    dot_path = _make_dot_file(tmp_path)

    async def fake_run_pipeline(dot_source, **kwargs):
        del kwargs
        return PipelineResult(status="success", notes="", logs_dir=tmp_path, raw="{}")

    monkeypatch.setattr(runner_mod, "run_pipeline", fake_run_pipeline)

    rc = cli.main(
        ["run", dot_path, "--cwd", str(tmp_path), "--logs-root", str(tmp_path)]
    )
    assert rc == 0

    captured = capsys.readouterr()
    # Notice text appears in stderr exactly once.
    assert captured.err.count("NOTICE") == 1
    assert "dot-runner" in captured.err
    # stdout is the pre-P3 shape: unaffected by the notice (stderr-only).
    assert "NOTICE" not in captured.out
    assert f"attractor: running pipeline cwd={tmp_path} logs={tmp_path}" in captured.out
    assert "attractor: status=success" in captured.out
    assert f"attractor: logs={tmp_path}" in captured.out


def test_dot_runner_personality_never_prints_the_attractor_notice(
    monkeypatch, tmp_path, capsys
):
    """`dot-runner` IS the engine-native command the notice points toward --
    it must never print its own deprecation notice."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    dot_path = _make_dot_file(tmp_path)

    async def fake_run_pipeline(dot_source, **kwargs):
        del kwargs
        return PipelineResult(status="success", notes="", logs_dir=tmp_path, raw="{}")

    monkeypatch.setattr(runner_mod, "run_pipeline", fake_run_pipeline)

    rc = cli.main_dot_runner(
        ["run", dot_path, "--cwd", str(tmp_path), "--logs-root", str(tmp_path)]
    )
    assert rc == 0

    captured = capsys.readouterr()
    assert "NOTICE" not in captured.err
    assert "NOTICE" not in captured.out
    assert (
        f"dot-runner: running pipeline cwd={tmp_path} logs={tmp_path}" in captured.out
    )
    assert "dot-runner: status=success" in captured.out


# ---------------------------------------------------------------------------
# 4. Entry-point registration / dispatch
# ---------------------------------------------------------------------------


def test_both_entry_points_registered_in_pyproject():
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert scripts["attractor"] == "amplifier_module_pipeline_runner.cli:main"
    assert (
        scripts["dot-runner"] == "amplifier_module_pipeline_runner.cli:main_dot_runner"
    )


def test_help_works_for_both_personalities():
    for entry, prog in ((cli.main, "attractor"), (cli.main_dot_runner, "dot-runner")):
        with pytest.raises(SystemExit) as exc_info:
            entry(["--help"])
        assert exc_info.value.code == 0


def test_dispatch_table_covers_all_five_subcommands():
    assert set(cli._DISPATCH) == {"run", "resume", "doctor", "trace", "lint"}


# ---------------------------------------------------------------------------
# 5. doctor/lint/trace smoke under the dot-runner personality
# ---------------------------------------------------------------------------


def test_doctor_smoke_under_dot_runner(capsys):
    rc = cli.main_dot_runner(["doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dot-runner doctor:" in out


def test_lint_smoke_under_dot_runner(tmp_path, capsys):
    """Pure-static smoke: lint is identical for both personalities (no
    engine_native branching in cmd_lint). A trivial linear graph is
    structurally clean (WARNING-only, exit 0) -- lint's ERROR/exit-1 path is
    already covered by the pre-existing suite (test_lint_folder_dot_file.py);
    this just proves the `dot-runner` personality reaches the same command.
    """
    dot_path = tmp_path / "clean.dot"
    dot_path.write_text(
        "digraph T { start [shape=Mdiamond]; done [shape=Msquare]; start -> done; }",
        encoding="utf-8",
    )
    rc = cli.main_dot_runner(["lint", str(dot_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dot-runner lint:" in out


def test_trace_smoke_under_dot_runner(tmp_path, capsys):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rc = cli.main_dot_runner(["trace", str(run_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dot-runner trace:" in out
    assert "no trace data" in out
