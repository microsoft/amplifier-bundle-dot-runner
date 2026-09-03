"""Test configuration for the conformance-ledger checks.

The checks under `ledger/checks/` live at the REPO ROOT, not inside a module,
because the ledger they execute is a repo-level artifact (`ledger/rows.yaml`)
that answers to a repo-level contract (`contracts/external/`).  Their behavioral
probes, however, drive the real engine -- so they need exactly the environment
`modules/loop-pipeline`'s own suite runs in: its source tree on `sys.path`, and
its stubs for the optional `amplifier_foundation` / `amplifier_core` peers
registered in `sys.modules` BEFORE any probe imports the backend.

Rather than fork that setup (two copies drift; the stub shapes are load-bearing
-- faking a kernel contract wrongly is precisely how CAL-10 shipped silently),
this conftest EXECUTES `modules/loop-pipeline/tests/conftest.py` in its own
module namespace.  One source of truth, loaded by path.

That file is required, not optional: if it moves or disappears, these checks
fail loudly here rather than half-importing and reporting a green ledger from a
suite that never ran.
"""

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_LOOP_PIPELINE = REPO_ROOT / "modules" / "loop-pipeline"
_SOURCE_CONFTEST = _LOOP_PIPELINE / "tests" / "conftest.py"

if not _SOURCE_CONFTEST.exists():  # pragma: no cover - fails loud by design
    raise RuntimeError(
        "ledger/checks/conftest.py cannot find the loop-pipeline test conftest it "
        f"reuses: {_SOURCE_CONFTEST}\n"
        "The ledger's behavioral probes drive the real engine and need that "
        "module's sys.path entry and its amplifier_foundation / amplifier_core "
        "stubs. Point this path at the moved file -- do NOT fork the stubs: two "
        "copies drift, and a stub that misdescribes the kernel contract is how a "
        "silent no-op ships green."
    )

# Make the engine importable exactly as the module's own suite does.
_module_src = str(_LOOP_PIPELINE)
if _module_src not in sys.path:
    sys.path.insert(0, _module_src)

# Execute the source conftest's module body (its stub registration is top-level).
_spec = importlib.util.spec_from_file_location(
    "_loop_pipeline_tests_conftest", _SOURCE_CONFTEST
)
assert _spec is not None and _spec.loader is not None
_source = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_source)

# Re-export anything pytest would collect as a fixture from the source conftest,
# so a fixture added there keeps working here without a second edit.
for _name in dir(_source):
    if _name.startswith("_"):
        continue
    _obj = getattr(_source, _name)
    if callable(_obj) and hasattr(_obj, "_pytestfixturefunction"):
        globals()[_name] = _obj
