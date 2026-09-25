set -euo pipefail

ROOT="$(pwd)"
CENSUS="$ROOT/.ai/census"
ac1=UNMET
ac3=UNMET
ac4=UNMET
ac5=UNMET

write_census() {
  printf '%s\n' \
    "AC-1: $ac1" \
    "AC-3: $ac3" \
    "AC-4: $ac4" \
    "AC-5: $ac5" > "$CENSUS"
}

# The census is also the diagnostic channel for infrastructure failures.  Keep
# the rows initialized before checking any executable or file prerequisite.
if ! mkdir -p "$ROOT/.ai"; then
  echo "verifier infrastructure failure: cannot create .ai" >&2
  exit 2
fi
if ! : > "$CENSUS"; then
  echo "verifier infrastructure failure: cannot write .ai/census" >&2
  exit 2
fi

on_exit() {
  rc=$?
  write_census || rc=2
  exit "$rc"
}
trap on_exit EXIT

if ! command -v python3 >/dev/null 2>&1; then
  echo "verifier infrastructure failure: python3 is required" >&2
  exit 2
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "verifier infrastructure failure: uv is required for locked regression suites" >&2
  exit 2
fi

# AC-1: exercise the public human-gate handler twice, then exercise the public
# command entry point's process-result mapping.  The handler probe deliberately
# supplies a real question/evidence/next-action payload because the shipped
# handler already refuses an unsubstantiated CI decision; the criterion still
# requires the observable terminal outcome and artifact for a valid escalation.
probe_ac1() {
  python3 - "$ROOT" <<'PY'
import asyncio
import json
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "modules" / "loop-pipeline"))
sys.path.insert(0, str(root / "modules" / "pipeline-runner"))
# The direct worker requires a configured provider even when this graph has no
# LLM node; this sentinel is consumed only by the worker bootstrap, never sent
# to a service by the no-LLM probe graph.
os.environ.setdefault("ANTHROPIC_API_KEY", "gate-probe")

try:
    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
    from amplifier_module_loop_pipeline.handlers.human import HumanGateHandler
    from amplifier_module_pipeline_runner.runner import run_pipeline
except Exception as exc:
    print(f"AC-1 subject import failure: {exc}", file=sys.stderr)
    raise SystemExit(1)


def neutral_id() -> str:
    return "n" + secrets.token_hex(12)


def build_case(question: str, first: str, second: str):
    # The gate id is the literal public graph id named by the criterion. Every
    # other synthetic witness is born at runtime and carries no domain word.
    start = Node(id=neutral_id(), shape="Mdiamond", label=neutral_id())
    gate = Node(id="escalate", shape="hexagon", label=question, prompt=question)
    left = Node(id=neutral_id(), shape="box", label=neutral_id())
    right = Node(id=neutral_id(), shape="box", label=neutral_id())
    graph = Graph(
        name=neutral_id(),
        nodes={n.id: n for n in (start, gate, left, right)},
        edges=[
            Edge(start.id, gate.id),
            Edge(gate.id, left.id, label=first),
            Edge(gate.id, right.id, label=second),
        ],
    )
    return graph, gate


def direct_case(question: str, first: str, second: str) -> bool:
    try:
        tmp = tempfile.TemporaryDirectory()
    except Exception as exc:
        print(f"AC-1 verifier input setup failure: {exc}", file=sys.stderr)
        raise SystemExit(2)
    with tmp:
        work = Path(tmp.name)
        try:
            graph, gate = build_case(question, first, second)
            context = PipelineContext()
            context.set("escalation.question", question)
            context.set("escalation.evidence", "conflict " + neutral_id())
            context.set("escalation.next_action", neutral_id())
        except Exception as exc:
            print(f"AC-1 verifier input construction failure: {exc}", file=sys.stderr)
            raise SystemExit(2)

        old_cwd = Path.cwd()
        try:
            os.chdir(work)
            try:
                outcome = asyncio.run(
                    HumanGateHandler().execute(gate, context, graph, str(work / "logs"))
                )
            except Exception as exc:
                print(f"AC-1 subject handler raised: {exc}", file=sys.stderr)
                return False
            status = getattr(getattr(outcome, "status", None), "value", None)
            artifact = work / ".ai" / "escalation.md"
            if status != "escalated":
                print(f"AC-1 unmet: observed handler status {status!r}", file=sys.stderr)
                return False
            try:
                text = artifact.read_text(encoding="utf-8")
            except FileNotFoundError:
                print("AC-1 unmet: .ai/escalation.md was not written", file=sys.stderr)
                return False
            except OSError as exc:
                print(f"AC-1 verifier artifact read failure: {exc}", file=sys.stderr)
                raise SystemExit(2)
            lines = text.splitlines()
            if question not in text or not any(question == line for line in lines):
                print("AC-1 unmet: artifact omitted the gate question", file=sys.stderr)
                return False
            for meaning in (first, second):
                if not any(meaning in line and "\n" not in line for line in lines):
                    print(f"AC-1 unmet: artifact omitted one-line option meaning {meaning!r}", file=sys.stderr)
                    return False
            if (work / ".ai" / "postmortem.md").exists() or (work / ".ai" / "postmortem").exists():
                print("AC-1 unmet: escalation produced a postmortem artifact", file=sys.stderr)
                return False
        finally:
            os.chdir(old_cwd)
    return True


first_question = "Q" + secrets.token_hex(10)
first_meaning = "M" + secrets.token_hex(10)
second_meaning = "N" + secrets.token_hex(10)
direct_ok = direct_case(first_question, "[A] " + first_meaning, "[C] " + second_meaning)

second_question = "Q" + secrets.token_hex(10)
second_meaning_a = "R" + secrets.token_hex(10)
second_meaning_b = "S" + secrets.token_hex(10)
direct_ok = direct_case(second_question, "[K] " + second_meaning_a, "[L] " + second_meaning_b) and direct_ok
if first_question == second_question or first_meaning == second_meaning_a:
    print("AC-1 verifier error: input variation was not material", file=sys.stderr)
    raise SystemExit(2)
if not direct_ok:
    print("AC-1 direct handler behavior is unmet; continuing to test the real runner boundary", file=sys.stderr)

# The positive path now crosses the real public runner/engine boundary.  The
# child invokes the actual CLI, which invokes run_pipeline and the engine; no
# runner function, result, handler, provider, or status is replaced by a fake.
# Runtime-born values make the expected artifact a data-dependent observation,
# rather than a branch keyed to a string copied from this verifier.
def dot_source(gate_id: str, start_id: str, first_id: str, second_id: str, exit_id: str, first: str, second: str, question: str) -> str:
    return (
        f'digraph {{ {start_id} [shape=Mdiamond]; '
        f'{gate_id} [shape=hexagon, type="wait.human", label="{question}"]; '
        f'{first_id} [shape=box]; {second_id} [shape=box]; {exit_id} [shape=Msquare]; '
        f'{start_id} -> {gate_id}; '
        f'{gate_id} -> {first_id} [label="[{neutral_id()}] {first}"]; '
        f'{gate_id} -> {second_id} [label="[{neutral_id()}] {second}"]; '
        f'{first_id} -> {exit_id}; {second_id} -> {exit_id}; }}'
    )

env = dict(os.environ)
env["PYTHONPATH"] = str(root / "modules" / "pipeline-runner") + os.pathsep + str(root / "modules" / "loop-pipeline")
env["ANTHROPIC_API_KEY"] = "gate-probe"

try:
    escalation_work = Path(tempfile.mkdtemp())
    failure_work = Path(tempfile.mkdtemp())
except OSError as exc:
    print(f"AC-1 verifier command-probe setup failure: {exc}", file=sys.stderr)
    raise SystemExit(2)

question = neutral_id()
first_meaning = neutral_id()
second_meaning = neutral_id()
evidence = "AC-1 " + neutral_id()
next_action = neutral_id()
escalation_source = dot_source(
    "escalate",
    neutral_id(),
    neutral_id(),
    neutral_id(),
    neutral_id(),
    first_meaning,
    second_meaning,
    question,
)
# Independently observe the public library seam before observing the CLI.  The
# engine result is not inferred from the command report, and no runner,
# handler, provider, or result is replaced.
try:
    direct_escalation_work = Path(tempfile.mkdtemp())
    direct_failure_work = Path(tempfile.mkdtemp())
    direct_escalation_logs = Path(tempfile.mkdtemp())
    direct_failure_logs = Path(tempfile.mkdtemp())
except OSError as exc:
    print(f"AC-1 verifier direct-run setup failure: {exc}", file=sys.stderr)
    raise SystemExit(2)

async def call_real_runner(source: str, work: Path, logs: Path, values: dict[str, str]):
    return await run_pipeline(
        source,
        params=values or None,
        cwd=work,
        logs_root=logs,
        provider="anthropic",
        worker="llm-direct",
    )

try:
    direct_escalation = asyncio.run(
        call_real_runner(
            escalation_source,
            direct_escalation_work,
            direct_escalation_logs,
            {
                "escalation.question": question,
                "escalation.evidence": evidence,
                "escalation.next_action": next_action,
            },
        )
    )
except Exception as exc:
    print(f"AC-1 subject run_pipeline escalation probe raised: {exc}", file=sys.stderr)
    raise SystemExit(1)

hard_start = neutral_id()
hard_work = neutral_id()
hard_exit = neutral_id()
hard_failure_source = (
    f'digraph {{ {hard_start} [shape=Mdiamond]; '
    f'{hard_work} [shape=parallelogram, tool_command="false"]; '
    f'{hard_exit} [shape=Msquare]; '
    f'{hard_start} -> {hard_work}; {hard_work} -> {hard_exit}; }}'
)
try:
    direct_failure = asyncio.run(
        call_real_runner(hard_failure_source, direct_failure_work, direct_failure_logs, {})
    )
except Exception as exc:
    print(f"AC-1 subject run_pipeline hard-failure control raised: {exc}", file=sys.stderr)
    raise SystemExit(1)

if getattr(direct_escalation, "status", None) != "escalated":
    print(
        f"AC-1 unmet: public run_pipeline returned {getattr(direct_escalation, 'status', None)!r}, not escalated",
        file=sys.stderr,
    )
    raise SystemExit(1)
if getattr(direct_failure, "status", None) == getattr(direct_escalation, "status", None):
    print("AC-1 unmet: run_pipeline hard-failure control did not differ from escalation", file=sys.stderr)
    raise SystemExit(1)

try:
    direct_artifact = (direct_escalation_work / ".ai" / "escalation.md").read_text(encoding="utf-8")
except FileNotFoundError:
    print("AC-1 unmet: public run_pipeline did not write .ai/escalation.md", file=sys.stderr)
    raise SystemExit(1)
except OSError as exc:
    print(f"AC-1 verifier direct-run artifact read failure: {exc}", file=sys.stderr)
    raise SystemExit(2)
direct_lines = direct_artifact.splitlines()
if not any(line == question for line in direct_lines):
    print("AC-1 unmet: run_pipeline artifact omitted the generated question", file=sys.stderr)
    raise SystemExit(1)
for meaning in (first_meaning, second_meaning):
    if not any(meaning in line for line in direct_lines):
        print(f"AC-1 unmet: run_pipeline artifact omitted generated option meaning {meaning!r}", file=sys.stderr)
        raise SystemExit(1)
if (direct_escalation_work / ".ai" / "postmortem.md").exists() or (direct_escalation_work / ".ai" / "postmortem").exists():
    print("AC-1 unmet: run_pipeline escalation produced a postmortem artifact", file=sys.stderr)
    raise SystemExit(1)

def run_command(source: str, work: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "from amplifier_module_pipeline_runner.cli import main; raise SystemExit(main())",
                "run",
                "--dot-source",
                source,
                "--worker",
                "llm-direct",
                "--provider",
                "anthropic",
                "--cwd",
                str(work),
                "--logs-root",
                str(work / "logs"),
                "--on-human-gate",
                "fail",
                "--param",
                "escalation.question=" + question,
                "--param",
                "escalation.evidence=" + evidence,
                "--param",
                "escalation.next_action=" + next_action,
            ],
            cwd=work,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        print(f"AC-1 subject command timed out: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except OSError as exc:
        print(f"AC-1 verifier command invocation failure: {exc}", file=sys.stderr)
        raise SystemExit(2)

escalated = run_command(escalation_source, escalation_work)
failed = run_command(hard_failure_source, failure_work)
if escalated.returncode == failed.returncode:
    print(
        f"AC-1 unmet: escalation and hard failure returned the same code {escalated.returncode}",
        file=sys.stderr,
    )
    raise SystemExit(1)
try:
    escalation_report = json.loads(escalated.stdout.strip().splitlines()[-1])
    failure_report = json.loads(failed.stdout.strip().splitlines()[-1])
except (json.JSONDecodeError, IndexError) as exc:
    print(f"AC-1 unmet: public command did not emit a JSON run report: {exc}", file=sys.stderr)
    raise SystemExit(1)
if escalation_report.get("status") != "escalated":
    print(
        f"AC-1 unmet: real runner/engine reported {escalation_report.get('status')!r}, not escalated",
        file=sys.stderr,
    )
    raise SystemExit(1)
if failed.returncode == 0:
    print("AC-1 verifier setup failure: hard-failure control unexpectedly succeeded", file=sys.stderr)
    raise SystemExit(2)
if escalated.returncode == failed.returncode:
    print("AC-1 unmet: escalation process result is not distinct from hard failure", file=sys.stderr)
    raise SystemExit(1)
try:
    command_artifact = (escalation_work / ".ai" / "escalation.md").read_text(encoding="utf-8")
except FileNotFoundError:
    print("AC-1 unmet: real runner/engine did not write .ai/escalation.md", file=sys.stderr)
    raise SystemExit(1)
except OSError as exc:
    print(f"AC-1 verifier artifact read failure: {exc}", file=sys.stderr)
    raise SystemExit(2)
command_lines = command_artifact.splitlines()
if not any(line == question for line in command_lines):
    print("AC-1 unmet: real-run artifact omitted the generated question", file=sys.stderr)
    raise SystemExit(1)
for meaning in (first_meaning, second_meaning):
    if not any(meaning in line for line in command_lines):
        print(f"AC-1 unmet: real-run artifact omitted generated option meaning {meaning!r}", file=sys.stderr)
        raise SystemExit(1)
if (escalation_work / ".ai" / "postmortem.md").exists() or (escalation_work / ".ai" / "postmortem").exists():
    print("AC-1 unmet: real-run escalation produced a postmortem artifact", file=sys.stderr)
    raise SystemExit(1)
if not any("escalation" in line.lower() for line in command_lines):
    print("AC-1 unmet: real-run artifact does not identify a CI escalation", file=sys.stderr)
    raise SystemExit(1)
PY
}

set +e
probe_ac1
probe_rc=$?
set -e
if [[ $probe_rc -eq 0 ]]; then
  ac1=MET
elif [[ $probe_rc -eq 1 ]]; then
  echo "AC-1 is unmet" >&2
else
  echo "AC-1 verifier infrastructure failed (rc=$probe_rc)" >&2
  exit 2
fi

# AC-3 [guard]: retain interactive choice routing through the public handler,
# with two different answers and two different observed destinations.
probe_ac3_interactive() {
  python3 - "$ROOT" <<'PY'
import asyncio
import secrets
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "modules" / "loop-pipeline"))
try:
    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
    from amplifier_module_loop_pipeline.handlers.human import HumanGateHandler
    from amplifier_module_loop_pipeline.interviewer import Answer, QueueInterviewer
except Exception as exc:
    print(f"AC-3 subject import failure: {exc}", file=sys.stderr)
    raise SystemExit(1)


def nid() -> str:
    return "n" + secrets.token_hex(10)


def one_case(answer: str, key: str) -> bool:
    gate = Node(id="escalate", shape="hexagon", label="Q" + secrets.token_hex(6))
    left = Node(id=nid(), shape="box", label=nid())
    right = Node(id=nid(), shape="box", label=nid())
    graph = Graph(
        name=nid(),
        nodes={n.id: n for n in (gate, left, right)},
        edges=[
            Edge(gate.id, left.id, label="[A] " + nid()),
            Edge(gate.id, right.id, label="[C] " + nid()),
        ],
    )
    expected = left.id if key == "A" else right.id
    try:
        result = asyncio.run(
            HumanGateHandler(
                interviewer=QueueInterviewer([Answer(value=answer)])
            ).execute(gate, PipelineContext(), graph, "/tmp")
        )
    except Exception as exc:
        print(f"AC-3 subject interactive probe raised: {exc}", file=sys.stderr)
        return False
    status = getattr(getattr(result, "status", None), "value", None)
    if status != "success" or getattr(result, "suggested_next_ids", None) != [expected]:
        print(f"AC-3 unmet for answer {answer!r}: {result!r}", file=sys.stderr)
        return False
    return True

if not one_case("A", "A") or not one_case("C", "C"):
    raise SystemExit(1)
PY
}
set +e
probe_ac3_interactive
probe_rc=$?
set -e
if [[ $probe_rc -eq 0 ]]; then
  if uv run --locked --project "$ROOT/modules/loop-pipeline" pytest -q "$ROOT/modules/loop-pipeline/tests/test_human.py"; then
    ac3=MET
  else
    test_rc=$?
    if [[ $test_rc -eq 1 ]]; then
      echo "AC-3 unmet: existing human-gate regression suite failed" >&2
    else
      echo "AC-3 verifier infrastructure failed while running regression suite (rc=$test_rc)" >&2
      exit 2
    fi
  fi
elif [[ $probe_rc -eq 1 ]]; then
  echo "AC-3 interactive behavior is unmet" >&2
else
  echo "AC-3 verifier infrastructure failed (rc=$probe_rc)" >&2
  exit 2
fi

# AC-4 [guard]: parse and lint every shipped graph, inspect every escalation
# target through the graph model, and check the forbidden wording in the named
# public graph/readme files.
probe_ac4() {
  python3 - "$ROOT" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "modules" / "loop-pipeline"))
try:
    from amplifier_module_loop_pipeline.dot_parser import parse_dot
    from amplifier_module_loop_pipeline.validation import lint
except Exception as exc:
    print(f"AC-4 subject import failure: {exc}", file=sys.stderr)
    raise SystemExit(1)

base = root / ".github" / "capsule-pipeline"
params = {
    "issue_file": "/tmp/issue",
    "criteria_file": "/tmp/criteria",
    "target_dir": "/tmp",
    "base_sha": "base",
    "later_commit": "",
    "uplift_dir": "/tmp/uplift",
    "capsule_out": "/tmp/capsule-out",
    "max_iterations": "8",
    "gate_time_ceiling": "5",
    "max_duration": "30m",
}
for name in ("capsule.dot", "feature-capsule.dot", "task-runner.dot"):
    path = base / name
    try:
        source = path.read_text(encoding="utf-8")
        graph = parse_dot(source, params=params)
        diagnostics = lint(graph)
    except FileNotFoundError as exc:
        print(f"AC-4 verifier prerequisite missing: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except OSError as exc:
        print(f"AC-4 verifier file-read failure: {exc}", file=sys.stderr)
        raise SystemExit(2)
    except Exception as exc:
        print(f"AC-4 subject parser/linter raised for {name}: {exc}", file=sys.stderr)
        raise SystemExit(1)
    errors = [d for d in diagnostics if getattr(d, "severity", None) == "ERROR"]
    if errors:
        print(f"AC-4 unmet: {name} has lint errors: {errors}", file=sys.stderr)
        raise SystemExit(1)
    for edge in graph.edges:
        if edge.to_node == "escalate" and edge.to_node not in graph.nodes:
            print(f"AC-4 unmet: {name} has an undeclared escalate target", file=sys.stderr)
            raise SystemExit(1)

for path in (base / "capsule.dot", base / "feature-capsule.dot", base / "task-runner.dot", root / "README.md"):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"AC-4 verifier file-read failure: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if "trapdoor" in text.lower():
        print(f"AC-4 unmet: forbidden wording occurs in {path}", file=sys.stderr)
        raise SystemExit(1)
PY
}
set +e
probe_ac4
probe_rc=$?
set -e
if [[ $probe_rc -eq 0 ]]; then
  ac4=MET
elif [[ $probe_rc -eq 1 ]]; then
  echo "AC-4 graph guard is unmet" >&2
else
  echo "AC-4 verifier infrastructure failed (rc=$probe_rc)" >&2
  exit 2
fi

# AC-5: validate the maintained extension record and every ledger row whose
# indexed assertion covers the human-gate test surface. The ledger suite then
# performs its repository-wide quote/index/conformance checks.
probe_ac5_records() {
  python3 - "$ROOT" <<'PY'
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
try:
    import yaml
except Exception as exc:
    print(f"AC-5 verifier prerequisite failure: PyYAML unavailable: {exc}", file=sys.stderr)
    raise SystemExit(2)

try:
    extensions_path = root / "specs" / "EXTENSIONS.md"
    ledger_path = root / "ledger" / "rows.yaml"
    extensions = extensions_path.read_text(encoding="utf-8")
    ledger_text = ledger_path.read_text(encoding="utf-8")
    rows = yaml.safe_load(ledger_text)
except (OSError, yaml.YAMLError) as exc:
    print(f"AC-5 verifier record failure: {exc}", file=sys.stderr)
    raise SystemExit(2)

# Do not bind to a section number: the public record may grow while the
# observable dated entry remains the required source of truth.
blocks = []
current = []
for line in extensions.splitlines():
    if line.startswith("## ") and current:
        blocks.append("\n".join(current))
        current = []
    current.append(line)
if current:
    blocks.append("\n".join(current))
required_entry = next(
    (
        block for block in blocks
        if "escalated" in block
        and "human gate handler" in block.lower()
        and "extension" in block.lower()
        and "spec-silent" in block.lower()
        and "2026-09-07" in block
        and "owner ruling" in block.lower()
    ),
    None,
)
if required_entry is None:
    print("AC-5 unmet: no extension entry satisfies the outcome, human-gate-handler, extension-classification, date, owner-ruling, and spec-silent requirements", file=sys.stderr)
    raise SystemExit(1)

if not isinstance(rows, list):
    print("AC-5 verifier record failure: ledger is not a top-level list", file=sys.stderr)
    raise SystemExit(2)

def indexed_mentions_human_surface(value):
    if isinstance(value, str):
        return "modules/loop-pipeline/tests/test_human.py" in value
    if isinstance(value, dict):
        return any(indexed_mentions_human_surface(v) for v in value.values())
    if isinstance(value, list):
        return any(indexed_mentions_human_surface(v) for v in value)
    return False

def relevant_rows(values):
    result = {}
    for row in values:
        if not isinstance(row, dict):
            continue
        assertion = row.get("assertion")
        indexed = assertion.get("indexed", []) if isinstance(assertion, dict) else []
        if indexed_mentions_human_surface(indexed):
            row_id = row.get("id")
            if not isinstance(row_id, str) or not row_id:
                print("AC-5 verifier record failure: relevant ledger row has no string id", file=sys.stderr)
                raise SystemExit(2)
            if row_id in result:
                print(f"AC-5 verifier record failure: duplicate relevant ledger row id {row_id}", file=sys.stderr)
                raise SystemExit(2)
            result[row_id] = row
    return result

human_rows = relevant_rows(rows)
if not human_rows:
    print("AC-5 unmet: no ledger row indexes the human-gate test surface", file=sys.stderr)
    raise SystemExit(1)

# AC-5 says that every existing human-gate ledger row is updated. Compare the
# complete relevant row block against the pinned base, without prescribing which
# field or prose layout carries the update. A newly added relevant row has no
# base block to compare and is itself an observable addition.
base_sha = "fb35263d598187f05f33908c36f72287b95f4bfb"
try:
    base_result = subprocess.run(
        ["git", "-C", str(root), "show", f"{base_sha}:ledger/rows.yaml"],
        check=True,
        capture_output=True,
        text=True,
    )
except FileNotFoundError as exc:
    print(f"AC-5 verifier infrastructure failure: git is unavailable: {exc}", file=sys.stderr)
    raise SystemExit(2)
except (OSError, subprocess.CalledProcessError) as exc:
    print(f"AC-5 verifier infrastructure failure: cannot read ledger at base_sha {base_sha}: {exc}", file=sys.stderr)
    raise SystemExit(2)
try:
    base_rows = yaml.safe_load(base_result.stdout)
except yaml.YAMLError as exc:
    print(f"AC-5 verifier infrastructure failure: base ledger is invalid YAML: {exc}", file=sys.stderr)
    raise SystemExit(2)
if not isinstance(base_rows, list):
    print("AC-5 verifier infrastructure failure: base ledger is not a top-level list", file=sys.stderr)
    raise SystemExit(2)
base_human_rows = relevant_rows(base_rows)
for row_id, base_row in base_human_rows.items():
    current_row = human_rows.get(row_id)
    if current_row is None:
        print(f"AC-5 unmet: base human-gate ledger row {row_id} is missing", file=sys.stderr)
        raise SystemExit(1)
    if current_row == base_row:
        print(f"AC-5 unmet: human-gate ledger row {row_id} was not updated relative to {base_sha}", file=sys.stderr)
        raise SystemExit(1)

for row_id, row in human_rows.items():
    contract = row.get("contract")
    if not isinstance(contract, dict):
        print(f"AC-5 unmet: human-gate ledger row {row_id} has no contract", file=sys.stderr)
        raise SystemExit(1)
    contract_file = contract.get("file")
    quote = contract.get("quote")
    if not isinstance(contract_file, str) or not isinstance(quote, str):
        print(f"AC-5 unmet: human-gate ledger row {row_id} lacks contract file/quote", file=sys.stderr)
        raise SystemExit(1)
    try:
        contract_text = (root / contract_file).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"AC-5 verifier contract-read failure: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if quote.strip() not in contract_text:
        print(f"AC-5 unmet: quote for human-gate ledger row {row_id} is not present verbatim in {contract_file}", file=sys.stderr)
        raise SystemExit(1)
    assertion = row.get("assertion")
    if not isinstance(assertion, dict) or assertion.get("kind") != "indexed":
        print(f"AC-5 unmet: human-gate ledger row {row_id} lacks an indexed assertion", file=sys.stderr)
        raise SystemExit(1)
    if not indexed_mentions_human_surface(assertion.get("indexed", [])):
        print(f"AC-5 unmet: human-gate ledger row {row_id} has no indexed human-gate test path", file=sys.stderr)
        raise SystemExit(1)
PY
}
set +e
probe_ac5_records
probe_rc=$?
set -e
if [[ $probe_rc -eq 0 ]]; then
  if uv run --locked --project "$ROOT/modules/loop-pipeline" pytest -q "$ROOT/ledger/checks/test_spec_conformance_matrix.py"; then
    ac5=MET
  else
    test_rc=$?
    if [[ $test_rc -eq 1 ]]; then
      echo "AC-5 unmet: ledger conformance suite failed" >&2
    else
      echo "AC-5 verifier infrastructure failed while running ledger suite (rc=$test_rc)" >&2
      exit 2
    fi
  fi
elif [[ $probe_rc -eq 1 ]]; then
  echo "AC-5 extension or ledger evidence is unmet" >&2
else
  echo "AC-5 verifier infrastructure failed (rc=$probe_rc)" >&2
  exit 2
fi

write_census
if [[ "$ac1" == MET && "$ac3" == MET && "$ac4" == MET && "$ac5" == MET ]]; then
  exit 0
fi
exit 1
