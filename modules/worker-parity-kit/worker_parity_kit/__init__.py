"""worker-parity-kit: pins the dot-runner worker seam with EXECUTED tests.

Not an Amplifier module (no ``amplifier.modules`` entry point) -- an
installable pytest library. A worker's own test tree does::

    from worker_parity_kit.suite import *  # noqa: F401,F403

    @pytest.fixture
    def worker_harness():
        return MyWorkerHarness()

See ``README.md`` for "why this exists" (three real incidents, the same bug
class recurring across two different worker mechanisms) and
``worker_parity_kit.protocol`` for the ``WorkerHarness``/``TurnResult``
contract this kit's suite drives.
"""

from __future__ import annotations

from .protocol import TurnResult, WorkerHarness

__all__ = ["TurnResult", "WorkerHarness"]
