"""attractor / dot-runner: run an arbitrary DOT pipeline standalone.

ONE shared implementation, TWO console-script personalities
(DESIGN-worker-registry-core-split.md P3, "one binary, two names"):

* ``attractor`` -- the legacy, opinionated entry point. UNCHANGED behavior:
  auto-loads the attractor pattern bundle, its provider->agent profiles map,
  and (via that bundle's own agent configs) ``loop-agent`` as the effective
  default worker. Prints ONE deprecation-window notice to stderr per
  invocation (never stdout -- see ``_print_deprecation_notice``).
* ``dot-runner`` -- the new, engine-native entry point. Default worker
  ``direct``; zero runtime reach into any pattern repo (no attractor-bundle
  fetch, no profiles auto-load, no ``session.spawn`` capability at all).

Both personalities share every subcommand (``run``/``resume``/``doctor``/
``trace``/``lint``), every argument, and every code path below -- the two
``main*`` entry points differ only in the ``prog``/``engine_native`` values
threaded through ``_main``. Fails loud: a missing provider API key, missing
DOT source, unknown worker name, or a pipeline error all print a clear
message and exit non-zero. No fallbacks, no synthetic success.

Subcommands:
    run       <dot_file>   run a DOT pipeline
    resume    <run-dir>    resume an interrupted run from its checkpoint
    doctor                 environment diagnostics
    trace     <run-dir>    print a human-readable iteration/descent summary
    lint      <dot_file>   static topological lint of a .dot pipeline file
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from . import runner
from .compat import IncompatibleEngineError, check_engine_compatibility
from .params import parse_params

# DESIGN-worker-registry-core-split.md P3 item 3: "a printed notice" on the
# legacy `attractor` entry point -- what's changing, that `dot-runner` is the
# engine-native command, that `attractor`'s opinionated behavior continues
# through the window. No date promises (per the design doc's own P3 window
# language: "flipping to the engine-native default when the recipes repo
# lands" -- no fixed date named there either). stderr-only: this CLI has no
# --quiet/--json output mode to respect (checked -- cmd_run/cmd_resume always
# print their human-readable lines and the trailing JSON line together; there
# is nothing here that parses `attractor`'s own stdout machine-readably that
# a stderr-only notice could corrupt).
_DEPRECATION_NOTICE = (
    "attractor: NOTICE -- this command's opinionated defaults (auto-loading "
    "the attractor pattern bundle, its provider->agent profiles map, and "
    "loop-agent as the effective default worker) are being split out from "
    "the plain engine. The engine-native command is now also available as "
    "`dot-runner` (default worker: `direct`; no bundle/profile auto-load; "
    "zero runtime reach into any pattern repo). `attractor` keeps today's "
    "opinionated behavior UNCHANGED through a deprecation window (no fixed "
    "date) -- switch to `dot-runner` only when you want the engine-native "
    "defaults instead of attractor's."
)


def _print_deprecation_notice() -> None:
    """Print the P3 deprecation-window notice -- stderr only, once."""
    print(_DEPRECATION_NOTICE, file=sys.stderr)


def build_parser(prog: str = "attractor") -> argparse.ArgumentParser:
    """Build the shared argument parser for either console-script personality.

    ``prog`` selects only the argparse-reported program name and the
    top-level description text -- every subcommand, argument, default, and
    help string below is IDENTICAL between personalities (the ``--worker``
    flag included: P1 deliberately deferred it to P3, and both personalities
    gained it together rather than forking the parser). The default
    ``prog="attractor"`` reproduces the pre-P3 parser byte-for-byte for every
    existing caller of ``build_parser()`` with no arguments.
    """
    if prog == "dot-runner":
        description = (
            "Run an arbitrary DOT pipeline directly via the engine's worker "
            "registry -- engine-native defaults (default worker: `direct`; "
            "no attractor-bundle or profile auto-load; zero runtime reach "
            "into any pattern repo)."
        )
    else:
        description = "Run an attractor DOT pipeline standalone."
    parser = argparse.ArgumentParser(prog=prog, description=description)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a DOT pipeline")
    run.add_argument("dot_file", nargs="?", help="path to a .dot file")
    run.add_argument(
        "--dot-source",
        help="inline DOT digraph string (alternative to dot_file)",
    )
    run.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="k=v",
        help=(
            "key=value param for $param expansion in node prompts (repeatable). "
            "Value may be @path/to/file to read the param's value from a file's "
            "full contents (curl-style; handy for multi-line content like a "
            "checkbox worklist or spec). Use @@literal for a literal value "
            "starting with '@' (e.g. --param handle=@@jdoe -> '@jdoe')."
        ),
    )
    run.add_argument(
        "--provider",
        default="anthropic",
        help="provider whose API key to preflight-check (default: anthropic)",
    )
    run.add_argument(
        "--worker",
        default=None,
        metavar="NAME",
        help=(
            "run-level worker-selection default (EXTENSIONS.md Sec40 / "
            "DESIGN-worker-registry-core-split.md P1 item 3, P3 item 2). "
            "Feeds the worker registry's `default_worker`; a node's own "
            "`worker=` attribute still wins over this flag. An unknown name "
            "is refused at run time with a clean error listing every "
            "registered worker -- never a stack trace. Omitted (the "
            "default): `dot-runner` resolves to `direct`; `attractor` "
            "leaves its existing capability-fallback chain (spawn if "
            "available, else direct) unaffected."
        ),
    )
    run.add_argument(
        "--logs-root",
        default=None,
        help="directory for run logs (default: a fresh tempdir)",
    )
    run.add_argument(
        "--cwd",
        default=None,
        help=(
            "working directory for the pipeline -- where box-node agents and "
            "tool-node commands write files (default: current directory; "
            "created if it doesn't exist). NOTE: for pipelines with box/agent "
            "nodes, run from within this directory (e.g. `cd <dir> && attractor "
            "run pipeline.dot --cwd .`) -- see KNOWN_ISSUES.md (loop-agent cwd)."
        ),
    )
    run.add_argument(
        "--on-human-gate",
        choices=("fail", "auto-approve", "console"),
        default="fail",
        help=(
            "how to handle a human-gate (hexagon) node. 'fail' (default): "
            "fail loud -- a pipeline that needs a human decision terminates "
            "with a clear error (run it where a human/UI can answer, or pass "
            "auto-approve or console). 'auto-approve': supply an "
            "auto-approving interviewer that selects the first choice at "
            "each gate (opt-in, non-interactive). 'console': answer gates "
            "interactively in this terminal (wires the engine's "
            "ConsoleInterviewer; requires a usable stdin)."
        ),
    )

    resume = sub.add_parser(
        "resume",
        help="resume an interrupted run from the checkpoint in its run directory",
        description=(
            "Resume an interrupted pipeline run (attractor-spec §5.3). The run "
            "continues IN PLACE in <run_dir>: nodes the checkpoint records as "
            "completed are not re-executed, restored context is visible to the "
            "nodes that follow, and the run's records append to the interrupted "
            "ones. Resume from the SAME working directory the interrupted run "
            "used -- file state written by tool/agent nodes lives there and the "
            "engine cannot verify it. A missing, corrupted, wrong-schema, "
            "already-completed or structurally-invalid checkpoint fails loud and "
            "exits non-zero; it never silently restarts from the start node."
        ),
    )
    resume.add_argument(
        "run_dir",
        help="run directory of the interrupted run (the one holding checkpoint.json)",
    )
    resume.add_argument(
        "--dot-file",
        default=None,
        help=(
            "optional .dot file for provenance. It must fingerprint-match the "
            "graph embedded in the checkpoint or the resume is refused -- a "
            "checkpoint binds to the run that wrote it. Omit it to resume "
            "against the checkpoint's own embedded graph (the normal case)."
        ),
    )
    resume.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="k=v",
        help=(
            "key=value param, same syntax as 'run'. On resume a param may only "
            "ADD context keys: colliding with a key restored from the "
            "checkpoint is an error, not a silent override."
        ),
    )
    resume.add_argument(
        "--provider",
        default="anthropic",
        help="provider whose API key to preflight-check (default: anthropic)",
    )
    resume.add_argument(
        "--worker",
        default=None,
        metavar="NAME",
        help="run-level worker-selection default, same as on 'run'.",
    )
    resume.add_argument(
        "--cwd",
        default=None,
        help=(
            "working directory for the resumed pipeline (default: current "
            "directory). Use the SAME directory the interrupted run used."
        ),
    )
    resume.add_argument(
        "--on-human-gate",
        choices=("fail", "auto-approve", "console"),
        default="fail",
        help="as on 'run' -- a human gate the run was parked at is re-asked",
    )

    sub.add_parser("doctor", help="environment diagnostics")

    trace = sub.add_parser(
        "trace",
        help="print a human-readable iteration/descent summary from a run directory",
    )
    trace.add_argument(
        "run_dir",
        help="path to the run directory produced by 'attractor run'",
    )

    lint_p = sub.add_parser(
        "lint",
        help="static topological lint of a .dot pipeline file",
    )
    lint_p.add_argument("dot_file", help="path to a .dot file to lint")
    lint_p.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="exit non-zero on WARNING diagnostics as well as ERRORs (default: only ERRORs cause non-zero exit)",
    )

    return parser


def _stdin_is_usable() -> bool:
    """Return True if ``sys.stdin`` is present and safe to read from.

    Piped (non-tty) stdin is explicitly allowed -- scripted/non-interactive
    answers via a pipe are a legitimate way to drive ``--on-human-gate
    console`` (e.g. in a script or a test). This function deliberately does
    NOT call ``isatty()`` -- a non-tty stream is not the same as an unusable
    one. Only a genuinely absent or closed stdin fails this check.
    """
    stdin = sys.stdin
    if stdin is None:
        return False
    try:
        if stdin.closed:
            return False
    except ValueError:
        # Some closed-stream implementations raise on `.closed` access.
        return False
    readable = getattr(stdin, "readable", None)
    if readable is None:
        return True
    try:
        return bool(readable())
    except ValueError:
        return False


def cmd_run(args: argparse.Namespace) -> int:
    # ``prog``/``engine_native`` are set by ``_main`` before dispatch. A
    # direct caller (e.g. a test building ``args`` straight from
    # ``build_parser().parse_args()``) gets the attractor-personality
    # defaults, unchanged from before this parameter existed.
    prog = getattr(args, "prog_name", "attractor")
    engine_native = getattr(args, "engine_native", False)

    # --- Resolve DOT source: --dot-source wins, else read dot_file ---
    source_dir: str | None = None
    if args.dot_source:
        dot_source = args.dot_source
    elif args.dot_file:
        dot_path = Path(args.dot_file).expanduser()
        if not dot_path.is_file():
            print(f"{prog}: DOT file not found: {dot_path}", file=sys.stderr)
            return 1
        dot_source = dot_path.read_text(encoding="utf-8")
        # We know where this DOT lives, so relative `dot_file=` refs in the ROOT
        # graph resolve against its own tree -- the same rule child graphs already
        # get (PipelineHandler sets child_graph.source_dir) and that a remote
        # package gets from its materialized entry. Without this the root's
        # source_dir is empty and resolve_dot_path falls through to
        # context.target_dir (--cwd), looking for sibling bricks in the workspace.
        # --dot-source has no file, so it keeps the old cwd-relative behavior.
        source_dir = str(dot_path.resolve().parent)
    else:
        print(
            f"{prog}: either a dot_file argument or --dot-source is required",
            file=sys.stderr,
        )
        return 1

    # --- Parse params (fail loud on malformed entries) ---
    try:
        params = parse_params(args.param)
    except ValueError as e:
        print(f"{prog}: {e}", file=sys.stderr)
        return 1

    # --- Fail loud: unknown --provider is a CLI-argument error ---
    if args.provider not in runner.PROVIDER_KEY_ENV:
        print(
            f"{prog}: unknown provider {args.provider!r}. Known providers: "
            f"{', '.join(sorted(runner.PROVIDER_KEY_ENV))}",
            file=sys.stderr,
        )
        return 1

    # --- Fail loud: provider API key must be present BEFORE we run anything ---
    key_env = runner.PROVIDER_KEY_ENV[args.provider]
    if not os.environ.get(key_env):
        print(
            f"{prog}: missing API key -- set {key_env} for provider {args.provider!r}",
            file=sys.stderr,
        )
        return 1

    # --- Resolve logs root ---
    if args.logs_root:
        logs_root = Path(args.logs_root).expanduser()
    else:
        logs_root = Path(tempfile.mkdtemp(prefix="attractor-run-"))

    # --- Resolve pipeline working directory ---
    if args.cwd:
        cwd = Path(args.cwd).expanduser().resolve()
    else:
        cwd = Path.cwd()

    # --- Human-gate policy: default fail-loud; auto-approve/console are opt-in ---
    #
    # Spec basis (specs/canonical/attractor-spec-canonical.md -- identical to
    # the fresh upstream clone at attractor/attractor-spec.md):
    #   Section 6.1 -- Interviewer interface (ask/ask_multiple/inform).
    #   Section 6.4 -- "ConsoleInterviewer (CLI): Reads from standard input.
    #     Displays formatted prompts with option keys."
    #   Conformance checklist (~line 1865): "ConsoleInterviewer prompts in
    #     terminal and reads user input."
    #   Section 9.5 -- human gates must be operable via CLI (web controls are
    #     additive on top of that baseline).
    # ConsoleInterviewer is an existing, public, spec-conformant
    # implementation (amplifier_module_loop_pipeline.interviewer); this wires
    # it into the runner the same way --on-human-gate auto-approve already
    # wires AutoApproveInterviewer -- no new interviewer behavior is added.
    # Freeform gate text (specs/EXTENSIONS.md Section 19, a declared bundle
    # extension) is already handled by ConsoleInterviewer.ask()'s FREEFORM
    # branch; nothing further to wire for it here.
    interviewer = None
    if args.on_human_gate == "auto-approve":
        from amplifier_module_loop_pipeline.interviewer import AutoApproveInterviewer

        interviewer = AutoApproveInterviewer()
    elif args.on_human_gate == "console":
        from amplifier_module_loop_pipeline.interviewer import ConsoleInterviewer

        # Fail loud at startup -- not at the first gate -- when stdin can't be
        # read at all. A piped, non-tty stdin is explicitly fine (scripted
        # answers are a legitimate way to drive this mode).
        if not _stdin_is_usable():
            print(
                f"{prog}: --on-human-gate console requires a usable stdin, "
                "but stdin is closed or unavailable. Run interactively, pipe "
                f"scripted answers in (e.g. `printf 'A\\n' | {prog} run "
                "... --on-human-gate console`), or use --on-human-gate "
                "auto-approve instead.",
                file=sys.stderr,
            )
            return 1
        interviewer = ConsoleInterviewer()

    print(f"{prog}: running pipeline cwd={cwd} logs={logs_root}")

    try:
        result = asyncio.run(
            runner.run_pipeline(
                dot_source,
                params=params or None,
                provider=args.provider,
                worker=args.worker,
                logs_root=logs_root,
                cwd=cwd,
                interviewer=interviewer,
                source_dir=source_dir,
                engine_native=engine_native,
            )
        )
    except Exception as e:  # noqa: BLE001 -- fail loud with the real error, no fallback
        print(
            f"{prog}: pipeline execution failed: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return 1

    print(f"{prog}: status={result.status}")
    print(f"{prog}: logs={result.logs_dir}")
    if result.notes:
        print(f"{prog}: notes:")
        print(result.notes)
    print(
        json.dumps(
            {
                "status": result.status,
                "notes": result.notes,
                "logs_dir": str(result.logs_dir),
            }
        )
    )

    if result.status != "success" and args.on_human_gate == "fail":
        print(
            f"{prog}: hint -- if this pipeline has a human-gate (hexagon) node, "
            "it fails loud by default. Re-run with --on-human-gate auto-approve to "
            "auto-approve gates non-interactively, --on-human-gate console to "
            "answer gates interactively in this terminal, or run it where a "
            "human/UI can answer the gate.",
            file=sys.stderr,
        )

    return 0 if result.status == "success" else 1


def cmd_resume(args: argparse.Namespace) -> int:
    """Resume an interrupted run from its checkpoint (attractor-spec §5.3).

    Fail-loud contract (AC-6 of issue #224): every way the checkpoint can be
    unusable -- missing, corrupted, wrong schema, already completed, written by
    a different graph, structurally invalid -- prints the named cause plus a
    remedy and exits non-zero. There is no fallback to a fresh run: a silent
    restart-from-scratch presented as a successful resume is exactly the
    outcome this command must never produce.
    """
    from amplifier_module_loop_pipeline.checkpoint import CheckpointResumeError

    prog = getattr(args, "prog_name", "attractor")
    engine_native = getattr(args, "engine_native", False)

    run_dir = Path(args.run_dir).expanduser()
    if not run_dir.is_dir():
        print(
            f"{prog} resume: run directory not found: {run_dir}",
            file=sys.stderr,
        )
        return 1

    dot_source: str | None = None
    if args.dot_file:
        dot_path = Path(args.dot_file).expanduser()
        if not dot_path.is_file():
            print(f"{prog} resume: DOT file not found: {dot_path}", file=sys.stderr)
            return 1
        dot_source = dot_path.read_text(encoding="utf-8")

    try:
        params = parse_params(args.param)
    except ValueError as e:
        print(f"{prog} resume: {e}", file=sys.stderr)
        return 1

    if args.provider not in runner.PROVIDER_KEY_ENV:
        print(
            f"{prog} resume: unknown provider {args.provider!r}. Known providers: "
            f"{', '.join(sorted(runner.PROVIDER_KEY_ENV))}",
            file=sys.stderr,
        )
        return 1

    key_env = runner.PROVIDER_KEY_ENV[args.provider]
    if not os.environ.get(key_env):
        print(
            f"{prog} resume: missing API key -- set {key_env} for provider "
            f"{args.provider!r}",
            file=sys.stderr,
        )
        return 1

    cwd = Path(args.cwd).expanduser().resolve() if args.cwd else Path.cwd()

    interviewer = None
    if args.on_human_gate == "auto-approve":
        from amplifier_module_loop_pipeline.interviewer import AutoApproveInterviewer

        interviewer = AutoApproveInterviewer()
    elif args.on_human_gate == "console":
        from amplifier_module_loop_pipeline.interviewer import ConsoleInterviewer

        if not _stdin_is_usable():
            print(
                f"{prog} resume: --on-human-gate console requires a usable "
                "stdin, but stdin is closed or unavailable.",
                file=sys.stderr,
            )
            return 1
        interviewer = ConsoleInterviewer()

    print(f"{prog}: resuming run cwd={cwd} logs={run_dir}")

    try:
        result = asyncio.run(
            runner.resume_pipeline(
                run_dir,
                dot_source=dot_source,
                params=params or None,
                provider=args.provider,
                worker=args.worker,
                cwd=cwd,
                interviewer=interviewer,
                engine_native=engine_native,
            )
        )
    except CheckpointResumeError as e:
        # The whole validation ladder lands here: named cause + remedy, no
        # fallback, non-zero exit, and nothing in the run directory touched.
        print(f"{prog} resume: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 -- fail loud with the real error
        print(
            f"{prog} resume: pipeline execution failed: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return 1

    print(f"{prog}: status={result.status}")
    print(f"{prog}: logs={result.logs_dir}")
    if result.notes:
        print(f"{prog}: notes:")
        print(result.notes)
    print(
        json.dumps(
            {
                "status": result.status,
                "notes": result.notes,
                "logs_dir": str(result.logs_dir),
            }
        )
    )

    return 0 if result.status == "success" else 1


def cmd_trace(args: argparse.Namespace) -> int:
    """Print a human-readable iteration/descent summary from a run directory.

    Reads ``trace.jsonl`` from the run directory (written by the engine on
    each node completion -- Extension #24) and prints a table of iterations,
    nodes, statuses, and durations. Exits 0 even if no trace data exists
    (e.g. runs that predate this feature) -- prints a clear "no trace data"
    message instead of crashing.
    """
    import collections

    prog = getattr(args, "prog_name", "attractor")

    run_dir = Path(args.run_dir).expanduser()
    if not run_dir.is_dir():
        print(
            f"{prog} trace: run directory not found: {run_dir}",
            file=sys.stderr,
        )
        return 1

    trace_path = run_dir / "trace.jsonl"
    if not trace_path.exists():
        print(
            f"{prog} trace: no trace data found in {run_dir}\n"
            "  (trace.jsonl is written by the engine on each node completion;\n"
            "   this run directory may predate convergence observability support)"
        )
        return 0

    records: list[dict] = []
    with open(trace_path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(
                    f"{prog} trace: warning — malformed record on line {lineno}: {e}",
                    file=sys.stderr,
                )

    if not records:
        print(f"{prog} trace: trace.jsonl exists but contains no records in {run_dir}")
        return 0

    # Group records by iteration for the summary view
    by_iteration: dict[int, list[dict]] = collections.defaultdict(list)
    for rec in records:
        iteration = rec.get("iteration", 0)
        by_iteration[iteration].append(rec)

    n_iterations = len(by_iteration)
    print(f"{prog} trace: {run_dir}")
    print(f"  iterations: {n_iterations}  total nodes: {len(records)}")
    print()

    for iteration in sorted(by_iteration.keys()):
        nodes = by_iteration[iteration]
        print(f"  iteration {iteration}  ({len(nodes)} node(s))")
        for rec in nodes:
            node_id = rec.get("node_id", "?")
            status = rec.get("status", "?")
            preferred = rec.get("preferred_label") or ""
            duration = rec.get("duration_ms")
            dur_str = f"  {duration:.0f}ms" if duration is not None else ""
            label_str = f"  [{preferred}]" if preferred else ""
            print(f"    {node_id:<20} {status:<16}{label_str}{dur_str}")
        print()

    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    """Static topological lint of a .dot pipeline file.

    Parses the DOT file and runs the full basin-lint rule set:
    structural rules (LINT-001–018), topological rules (TOPO-001–010),
    and command-content rules (CMD-001–002, which inspect tool_command
    strings for pipe-masked exit codes and always-true sentinels).

    Exit-code contract:
        ERROR-severity diagnostics → exit 1.
        WARNING-only (or clean) → exit 0 (unless --strict).
        --strict: exit 1 on any diagnostic (ERROR or WARNING).

    This command does not run the pipeline. It is safe to use in CI
    before committing a .dot file. Pure-static -- identical for both
    console-script personalities, so it needs no engine_native branching.
    """
    prog = getattr(args, "prog_name", "attractor")

    dot_path = Path(args.dot_file).expanduser()
    if not dot_path.is_file():
        print(f"{prog} lint: DOT file not found: {dot_path}", file=sys.stderr)
        return 1

    dot_source = dot_path.read_text(encoding="utf-8")

    # Import the pipeline module's parser and lint function
    try:
        from amplifier_module_loop_pipeline.dot_parser import parse_dot
        from amplifier_module_loop_pipeline.validation import lint
    except ImportError as e:
        print(
            f"{prog} lint: cannot import lint engine: {e}",
            file=sys.stderr,
        )
        return 1

    try:
        graph = parse_dot(dot_source)
    except Exception as e:  # noqa: BLE001 -- fail loud with the real error, no fallback
        print(f"{prog} lint: failed to parse {dot_path}: {e}", file=sys.stderr)
        return 1

    # Seed source_dir exactly as cmd_run does (the directory of the .dot file
    # that produced this graph -- EXTENSIONS.md §10 tier 2). TOPO-010 needs it
    # to resolve a static relative dot_file= the same way the engine will at run
    # time; without it the rule cannot tell where a relative target points and
    # deliberately stays silent. No other lint rule reads source_dir.
    graph.source_dir = str(dot_path.resolve().parent)

    diags = lint(graph)

    if not diags:
        print(f"{prog} lint: {dot_path}: OK (no findings)")
        return 0

    errors = [d for d in diags if d.severity == "ERROR"]
    warnings = [d for d in diags if d.severity == "WARNING"]
    infos = [d for d in diags if d.severity not in ("ERROR", "WARNING")]

    for d in diags:
        loc = f" [{d.node_id}]" if d.node_id else ""
        edge_loc = f" ({d.edge[0]} -> {d.edge[1]})" if d.edge else ""
        print(f"{d.severity}: [{d.rule}]{loc}{edge_loc} {d.message}")
        if d.fix:
            print(f"  fix: {d.fix}")

    print()
    summary_parts = []
    if errors:
        summary_parts.append(f"{len(errors)} error(s)")
    if warnings:
        summary_parts.append(f"{len(warnings)} warning(s)")
    if infos:
        summary_parts.append(f"{len(infos)} info(s)")
    print(f"{prog} lint: {dot_path}: {', '.join(summary_parts)}")

    if errors:
        return 1
    if args.strict and (warnings or infos):
        return 1
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    prog = getattr(args, "prog_name", "attractor")

    ok = True

    try:
        import amplifier_module_loop_pipeline  # noqa: F401
    except ImportError as e:
        print(
            f"{prog} doctor: FAIL -- amplifier_module_loop_pipeline not importable: {e}"
        )
        ok = False
    else:
        print(f"{prog} doctor: OK -- amplifier_module_loop_pipeline importable")

    for provider, env_name in sorted(runner.PROVIDER_KEY_ENV.items()):
        present = bool(os.environ.get(env_name))
        status = "present" if present else "absent"
        print(f"{prog} doctor: {provider} ({env_name}): {status}")

    return 0 if ok else 1


_DISPATCH = {
    "run": cmd_run,
    "resume": cmd_resume,
    "doctor": cmd_doctor,
    "trace": cmd_trace,
    "lint": cmd_lint,
}


def _main(argv: list[str] | None, *, prog: str, engine_native: bool) -> int:
    """Shared entry-point body for both console-script personalities.

    Fail loud at startup if the installed engine is incompatible with this
    runner. This catches version-skew before any node runs (incident
    2026-07-28: remote_dot absent in cached engine caused mid-run crash).
    """
    try:
        check_engine_compatibility()
    except IncompatibleEngineError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if not engine_native:
        # Legacy personality only -- `dot-runner` never prints this (it IS
        # the engine-native command the notice points toward).
        _print_deprecation_notice()

    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    args.prog_name = prog
    args.engine_native = engine_native

    return _DISPATCH[args.command](args)


def main(argv: list[str] | None = None) -> int:
    """``attractor`` console-script entry point -- unchanged personality."""
    return _main(argv, prog="attractor", engine_native=False)


def main_dot_runner(argv: list[str] | None = None) -> int:
    """``dot-runner`` console-script entry point -- engine-native personality."""
    return _main(argv, prog="dot-runner", engine_native=True)


if __name__ == "__main__":
    raise SystemExit(main())
