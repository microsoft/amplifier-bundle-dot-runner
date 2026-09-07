"""Freshness floor: "was this file written by THIS node execution?".

Two engine sites ask that question by comparing a file's mtime against
``node_start_wall`` -- a ``time.time()`` snapshot taken immediately before
the node's first attempt ran:

  * ``must_write.py`` (EXTENSIONS.md Sec 27) -- the declared artifact.
  * ``status_file.py`` (EXTENSIONS.md Sec 41) -- a node-written ``status.json``.

Both originally spelled it ``mtime <= node_start_wall -> not fresh``. That
compares two clocks with **different resolutions**. ``time.time()`` is
fine-grained; a file's mtime is stamped by the filesystem, and several
real filesystems stamp whole seconds (ext2, vfat, HFS+, NFSv2, and a
number of container/network mounts). On such a host:

    node_start_wall = 1000.4      (time.time(), fine-grained)
    file written at  1000.6       (genuinely during this execution)
    mtime stamped as 1000.0       (1-second-granularity filesystem)
    1000.0 <= 1000.4              -> "written before node start"  # WRONG

The consequences are the opposite of what each floor exists to do:
``must_write=`` FAILs a node that did write its artifact, and a valid
``status.json`` verdict is silently discarded as a stale leftover. The
failure is *nondeterministic* -- it fires only when ``node_start_wall``
happens to be sampled late enough within the filesystem's tick -- which is
how it presented: as a flaky test pool rather than a reproducible bug
(issue #67).

**The tolerance, and its exact bound.** A timestamp that lands on an exact
multiple of some power-of-ten nanosecond unit is evidence that its source
cannot resolve any finer than that unit, so a file stamped there may have
been written anywhere in the interval ``[mtime, mtime + granularity)``.
This module therefore lowers the floor by *exactly that evidenced
granularity* and no further, capped at one second.

What that does and does not cost:

  * On a fine-grained source (ext4, xfs, btrfs, tmpfs -- the common case,
    and the case in CI) the derived granularity is ``0.0`` and the
    comparison is **byte-for-byte the original strictly-greater-than**.
    The equality-boundary bypass that ``must_write.py`` calls out (an
    adversary setting mtime to exactly ``node_start_wall`` via
    ``os.utime``) stays rejected;
    ``test_engine_must_write.py::test_case3b_mtime_equals_start_fails``
    pins it.
  * On a 1-second-granular source, a file stamped in the same second the
    node started is accepted. A file planted up to one second before node
    start can therefore pass a floor it would previously have failed. That
    window is the filesystem's own uncertainty: on such a host the engine
    genuinely cannot distinguish "written 200ms before the node started"
    from "written 200ms after", and failing every honest node is the worse
    of the two errors.
  * A source with non-power-of-ten quantisation (e.g. a jiffy-quantised
    clock at CONFIG_HZ=250, stamping multiples of 4ms) is only partially
    covered: the derivation reads it as 1ms, leaving a ~3ms residual
    window. No such host has been observed for this failure; widening the
    derivation to cover it would be speculative and is deliberately not
    done here.
"""

from __future__ import annotations

_NS_PER_SECOND = 1_000_000_000

# Powers of ten from one second down to one microsecond. One second is the
# cap: it is the coarsest real filesystem granularity, and lowering a
# freshness floor by more than that would stop being a resolution
# correction and start being a hole.
# The floor is 1 ms, not something finer: no real filesystem quantises
# below it, so a finer bucket could only ever fire on a fine-grained stamp
# that landed on a round value by chance -- pure false-positive surface. It
# is not hypothetical: a 1 us bucket fires on roughly 1 in 2000 of the
# float-seconds timestamps `os.utime` round-trips, which was enough to make
# test_engine_must_write.py::test_case3b_equality_boundary_fails flaky at
# ~0.05% per run during this fix's own development.
_GRANULARITY_DIVISORS_NS: tuple[int, ...] = (
    1_000_000_000,  # 1 s      -- ext2, vfat, HFS+, NFSv2, some container mounts
    100_000_000,  # 100 ms
    10_000_000,  # 10 ms    -- jiffy-quantised clock at CONFIG_HZ=100
    1_000_000,  # 1 ms      -- jiffy-quantised clock at CONFIG_HZ=1000
)


def mtime_granularity_seconds(mtime_ns: int) -> float:
    """Resolution the timestamp itself evidences, in seconds (0.0 if fine).

    A stamp that is an exact multiple of some unit is evidence its source
    quantises to that unit. The coarsest matching unit wins, capped at one
    second (see module docstring). A fine-grained stamp matches nothing and
    returns ``0.0``, which makes every caller's comparison exactly the
    original strictly-greater-than.

    A fine-grained stamp can of course land on a round value by chance --
    one in a million for the finest unit (1 ms), one in a billion for the
    coarsest (1 s). The only consequence is a correspondingly tiny
    tolerance granted for that single comparison. See
    ``_GRANULARITY_DIVISORS_NS`` for why the finest unit is 1 ms and not
    something smaller.
    """
    if mtime_ns <= 0:
        return 0.0
    for divisor_ns in _GRANULARITY_DIVISORS_NS:
        if mtime_ns % divisor_ns == 0:
            return divisor_ns / _NS_PER_SECOND
    return 0.0


def wrote_after_node_start(mtime_ns: int, node_start_wall: float) -> bool:
    """True when ``mtime_ns`` post-dates ``node_start_wall``.

    The comparison is strictly-greater-than, lowered by the granularity
    ``mtime_ns`` itself evidences -- so a file a coarse filesystem stamped
    at the start of the tick the node began in still counts as written by
    this execution, while anything older does not.

    **Exact equality is never tolerated**, whatever the granularity. An
    mtime that equals ``node_start_wall`` to the nanosecond is the
    adversarial ``os.utime``-to-exact-start bypass ``must_write.py``'s
    docstring calls out, and it is the one case the tolerance must not
    reach: a coarse filesystem's stamp is a *floor* of the real write time,
    so it lands strictly below a mid-tick ``node_start_wall``, never on it.
    Granting equality would therefore forgive nothing real and open the
    boundary the floor exists to close.
    """
    mtime = mtime_ns / _NS_PER_SECOND
    if mtime > node_start_wall:
        return True
    if mtime == node_start_wall:
        return False
    return mtime > node_start_wall - mtime_granularity_seconds(mtime_ns)
