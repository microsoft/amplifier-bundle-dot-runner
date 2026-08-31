"""Tests for the `dot-runner` CLI -- the only console-script personality
after the `attractor` band-aid rip (CONTEXT_POISONING doctrine: no
attractor-specific policy in this engine repo; full removal, no alias/shim).

Covers:
  1. `dot-runner run` on a minimal box-node .dot with a FAKE provider
     (hermetic): executes via the `direct` worker, zero bundle/profile
     resolution attempted for a bare run.
  2. `--worker` flag: selects a registered worker; unknown name -> clean
     error listing registered workers (exit code + message, no traceback).
  3. WAVE 5 repair (2026-08-30): `--bundle`/`DOT_RUNNER_BUNDLE` are REMOVED
     from this CLI's surface entirely -- no flag, no env var, help text
     never mentions either (maintainer ruling: "bundles are under the
     hood -- never exposed to runner users"). `runner.run_pipeline`'s own
     `bundle=` parameter still exists as an internal mechanism
     (`default_worker.py` synthesizes one under the hood for a named
     worker) -- see test_default_worker.py for that coverage -- but the CLI
     itself never accepts or surfaces a bundle reference from the user.
  4. Entry-point registration: `main` dispatches correctly and `--help` works.
  5. doctor/lint/trace smoke.

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


def _forbid_load_named_bundle(monkeypatch) -> list[str]:
    """Monkeypatch ``_load_named_bundle`` to record/forbid any call.

    Returns the call-count list (mutated in place) so a test can assert
    ``calls == []`` -- the hermetic proof that no explicit bundle fetch was
    ever attempted (the network-reaching path this repo now only ever
    reaches on an EXPLICIT ``--bundle``/``bundle=``).
    """
    calls: list[str] = []

    async def spy(*_a, **_k):
        calls.append("called")
        raise AssertionError(
            "no bundle was given -- _load_named_bundle must never be called "
            "(mechanism, not policy: this engine fetches a pattern-repo "
            "bundle ONLY when the caller explicitly asks for one)."
        )

    monkeypatch.setattr(runner_mod, "_load_named_bundle", spy)
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
# 1. Bare by default -- no bundle fetch, no spawn capability
# ---------------------------------------------------------------------------


def test_build_prepared_bare_never_loads_a_named_bundle(monkeypatch, tmp_path):
    """The structural proof: `_build_prepared(base_bundle=None)` never calls
    `_load_named_bundle` -- it calls `_bare_base_bundle()` instead."""
    calls = _forbid_load_named_bundle(monkeypatch)
    _patch_bare_base_bundle(monkeypatch)

    prepared = asyncio.run(
        runner_mod._build_prepared(
            "digraph { start [shape=box]; }",
            tmp_path,
            params=None,
            profiles=None,
            worker="llm-direct",
        )
    )

    assert calls == []
    assert prepared is not None


def test_run_pipeline_bare_registers_no_spawn_capability(monkeypatch, tmp_path):
    """`run_pipeline()` with no `bundle=` never registers `session.spawn` --
    the mechanism that makes the `direct` worker the ONLY reachable path
    (no implicit profile resolution attempted)."""
    _patch_bare_base_bundle(monkeypatch)
    calls = _forbid_load_named_bundle(monkeypatch)

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
        )
    )

    assert result.status == "success"
    assert calls == []
    # No session.spawn ever registered -- direct worker is the only path.
    assert "session.spawn" not in captured["coordinator"].registered
    # Zero implicit profile fallback.
    assert captured["profiles"] == {}
    # Bare default worker resolves to "llm-direct" when unspecified.
    assert captured["default_worker"] == "llm-direct"


# ---------------------------------------------------------------------------
# 2. --bundle mechanism: explicit declaration enables session.spawn and
#    honors the referenced bundle's own declared worker/profiles.
# ---------------------------------------------------------------------------


def test_run_pipeline_bundle_registers_spawn_and_honors_declared_defaults(
    monkeypatch, tmp_path
):
    """`run_pipeline(bundle=...)` loads the named bundle, registers
    `session.spawn`, and -- absent an explicit `worker=`/`profiles=`
    override -- honors THAT bundle's own declared `session.orchestrator.config`
    as this run's effective default. Zero attractor-specific (or any other
    pattern-specific) knowledge lives in this engine repo -- it is proven
    here with an arbitrary fake bundle, not a real attractor one."""

    declared_bundle = FakeBundle(
        session={
            "orchestrator": {
                "config": {
                    "worker": "spawn",
                    "profiles": {"anthropic": "some-agent"},
                }
            }
        }
    )

    async def fake_load_named_bundle(ref):
        assert ref == "git+https://example.invalid/some-bundle.yaml"
        return declared_bundle

    monkeypatch.setattr(runner_mod, "_load_named_bundle", fake_load_named_bundle)

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
            bundle="git+https://example.invalid/some-bundle.yaml",
        )
    )

    assert result.status == "success"
    assert "session.spawn" in captured["coordinator"].registered
    assert captured["profiles"] == {"anthropic": "some-agent"}
    assert captured["default_worker"] == "spawn"


def test_run_pipeline_bundle_explicit_worker_overrides_declared(monkeypatch, tmp_path):
    """An explicit `worker=` always wins over the loaded bundle's own
    declared default."""
    declared_bundle = FakeBundle(
        session={"orchestrator": {"config": {"worker": "spawn"}}}
    )

    async def fake_load_named_bundle(ref):
        return declared_bundle

    monkeypatch.setattr(runner_mod, "_load_named_bundle", fake_load_named_bundle)

    captured: dict = {}

    async def fake_drive_engine(dot_source, coordinator, **kwargs):
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
            bundle="git+https://example.invalid/some-bundle.yaml",
            worker="llm-direct",
        )
    )

    assert captured["default_worker"] == "llm-direct"


# ---------------------------------------------------------------------------
# WAVE 5 repair regression: --bundle / DOT_RUNNER_BUNDLE must be GONE from
# the CLI surface entirely -- no flag, no env var fallback, and argparse
# must reject an attempt to pass one (never silently ignore it).
# ---------------------------------------------------------------------------


def test_cli_rejects_bundle_flag(monkeypatch, tmp_path, capsys):
    """--bundle is not a recognized flag anymore -- argparse fails loud
    (exit 2, "unrecognized arguments"), it is never silently accepted."""
    dot_path = _make_dot_file(tmp_path)
    parser = cli.build_parser(prog="dot-runner")
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(
            [
                "run",
                dot_path,
                "--bundle",
                "git+https://example.invalid/x.yaml",
                "--cwd",
                str(tmp_path),
            ]
        )
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "--bundle" in err
    assert "unrecognized" in err


def test_cli_dot_runner_bundle_env_var_has_no_effect(monkeypatch, tmp_path):
    """DOT_RUNNER_BUNDLE is no longer consulted anywhere -- a leftover
    value in the environment (e.g. from a pre-repair install/shell rc
    file) must have zero effect on the run."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    monkeypatch.setenv("DOT_RUNNER_BUNDLE", "git+https://example.invalid/env.yaml")
    # Amplifier-agent unavailable in this hermetic env -- falls back to direct.
    dot_path = _make_dot_file(tmp_path)

    captured: dict = {}

    async def fake_run_pipeline(dot_source, **kwargs):
        captured["bundle"] = kwargs.get("bundle")
        captured["worker"] = kwargs.get("worker")
        return PipelineResult(status="success", notes="", logs_dir=tmp_path, raw="{}")

    monkeypatch.setattr(runner_mod, "run_pipeline", fake_run_pipeline)

    parser = cli.build_parser(prog="dot-runner")
    args = parser.parse_args(
        ["run", dot_path, "--worker", "llm-direct", "--cwd", str(tmp_path)]
    )
    args.prog_name = "dot-runner"

    rc = cli.cmd_run(args)
    assert rc == 0
    assert captured["bundle"] is None, (
        "DOT_RUNNER_BUNDLE must not reach run_pipeline as a bundle= arg -- "
        f"got {captured['bundle']!r}"
    )
    assert captured["worker"] == "llm-direct"


def test_help_text_has_zero_bundle_vocabulary(capsys):
    """--help output for both run and resume must never mention --bundle
    or DOT_RUNNER_BUNDLE (maintainer ruling: bundles are under the hood)."""
    parser = cli.build_parser(prog="dot-runner")
    for sub in ("run", "resume"):
        with pytest.raises(SystemExit):
            parser.parse_args([sub, "--help"])
        out = capsys.readouterr().out
        assert "--bundle" not in out, f"{sub} --help must not mention --bundle:\n{out}"
        assert "DOT_RUNNER_BUNDLE" not in out, (
            f"{sub} --help must not mention DOT_RUNNER_BUNDLE:\n{out}"
        )


# ---------------------------------------------------------------------------
# 3. --worker flag: registered selection + clean unknown-name error
# ---------------------------------------------------------------------------


def test_worker_flag_accepted_by_argparse():
    parser = cli.build_parser(prog="dot-runner")
    args = parser.parse_args(["run", "dummy.dot", "--worker", "llm-direct"])
    assert args.worker == "llm-direct"
    args2 = parser.parse_args(["resume", "some-dir", "--worker", "llm-direct"])
    assert args2.worker == "llm-direct"


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
        return PipelineResult(status="success", notes="", logs_dir=tmp_path, raw="{}")

    monkeypatch.setattr(runner_mod, "run_pipeline", fake_run_pipeline)

    parser = cli.build_parser(prog="dot-runner")
    args = parser.parse_args(
        ["run", dot_path, "--worker", "llm-direct", "--cwd", str(tmp_path)]
    )
    args.prog_name = "dot-runner"

    rc = cli.cmd_run(args)
    assert rc == 0
    assert captured["worker"] == "llm-direct"


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

    rc = cli.cmd_run(args)

    assert rc == 1
    err = capsys.readouterr().err
    assert "totally-bogus-worker" in err
    assert "Traceback" not in err
    assert "pipeline execution failed" in err


# ---------------------------------------------------------------------------
# 4. Entry-point registration / dispatch -- `dot-runner` is the ONLY command
# ---------------------------------------------------------------------------


def test_only_dot_runner_script_registered_in_pyproject():
    from pathlib import Path

    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert set(scripts) == {"dot-runner"}
    assert scripts["dot-runner"] == "amplifier_module_pipeline_runner.cli:main"
    assert "attractor" not in scripts


def test_root_same_repo_dependencies_are_direct_refs_for_no_sources_installs():
    """Foundation ignores local uv source mappings when it activates a bundle."""
    from pathlib import Path

    import tomllib

    root_pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    data = tomllib.loads(root_pyproject.read_text(encoding="utf-8"))
    dependencies = data["project"]["dependencies"]
    sources = data["tool"]["uv"]["sources"]

    expected = {
        "amplifier-module-pipeline-runner": "modules/pipeline-runner",
        "amplifier-module-loop-amplifier-agent": "modules/loop-amplifier-agent",
        "amplifier-module-loop-agent": "modules/loop-agent",
    }
    for package, subdirectory in expected.items():
        direct_reference = (
            f"{package} @ "
            "git+https://github.com/microsoft/amplifier-bundle-dot-runner@main"
            f"#subdirectory={subdirectory}"
        )
        assert direct_reference in dependencies
        assert package not in dependencies
        assert sources[package] == {"path": subdirectory}

    assert data["tool"]["hatch"]["metadata"]["allow-direct-references"] is True


def test_help_works():
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])
    assert exc_info.value.code == 0


def test_dispatch_table_covers_all_five_subcommands():
    assert set(cli._DISPATCH) == {"run", "resume", "doctor", "trace", "lint"}


# ---------------------------------------------------------------------------
# 5. doctor/lint/trace smoke
# ---------------------------------------------------------------------------


def test_doctor_smoke(capsys):
    rc = cli.main(["doctor"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dot-runner doctor:" in out


def test_lint_smoke(tmp_path, capsys):
    """Pure-static smoke: lint's ERROR/exit-1 path is already covered by the
    pre-existing suite (test_lint_folder_dot_file.py); this just proves a
    trivial linear graph is structurally clean (WARNING-only, exit 0)."""
    dot_path = tmp_path / "clean.dot"
    dot_path.write_text(
        "digraph T { start [shape=Mdiamond]; done [shape=Msquare]; start -> done; }",
        encoding="utf-8",
    )
    rc = cli.main(["lint", str(dot_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dot-runner lint:" in out


def test_trace_smoke(tmp_path, capsys):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    rc = cli.main(["trace", str(run_dir)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dot-runner trace:" in out
    assert "no trace data" in out
