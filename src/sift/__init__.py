# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sift.

This module runs before anything else in the package, which is the only reason it holds code at
all: the settings below have to be in place before a numeric library is imported, and every other
module in the tree is imported after this one.
"""

from __future__ import annotations

import os
import sys

#: The numeric libraries under numpy start a thread pool on first load, and with that pool live a
#: process launch (fork or spawn alike) can deadlock in its handlers, leaving a server that answers
#: nothing. One thread is cheap for the bounded work here. Read once when the library loads, so set
#: anywhere later they would silently do nothing.
NUMERIC_THREAD_LIMITS = (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def _hold_numeric_libraries_to_one_thread() -> dict[str, str]:
    """Pin every numeric thread pool, returning any value that was overridden.

    Assigned rather than defaulted: a larger value is a server that stops answering under load, so
    it is replaced and reported rather than obeyed.
    """
    replaced: dict[str, str] = {}
    for name in NUMERIC_THREAD_LIMITS:
        was = os.environ.get(name)
        if was is not None and was != "1":
            replaced[name] = was
        os.environ[name] = "1"
    return replaced


#: What was found already set, for the boot line that reports it. Read by the composition root.
NUMERIC_THREADS_REPLACED = _hold_numeric_libraries_to_one_thread()


#: How long a thread may hold the interpreter before offering it to another, in seconds. `sqlite3`
#: gives the turn up once per ROW, so at the default a read of thousands of rows queues thousands of
#: times behind busy threads. Half a millisecond cuts that latency by an order of magnitude for a
#: small cost in CPU-bound throughput. A floor under the damage, not a licence to fetch rows.
GIL_SWITCH_SECONDS = 0.0005

sys.setswitchinterval(GIL_SWITCH_SECONDS)
