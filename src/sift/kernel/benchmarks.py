# SPDX-License-Identifier: AGPL-3.0-or-later
"""The scan history's component, retired: it creates nothing.

The work ledger (`kernel/jobs/ledger.py`) records every family's runs, and the scan history's table
is gone. The component stays registered because a library records its version: a name missing from
the registry reads as a component this build has never heard of, which is the newer-library refusal.
"""

from __future__ import annotations

from sift.kernel.db import Connection, register_schema_initializer

COMPONENT = "benchmarks"
VERSION = 3


async def initialize_benchmarks(connection: Connection, on_disk: int) -> None:
    """Nothing to create."""
    del connection, on_disk


register_schema_initializer(COMPONENT, VERSION, initialize_benchmarks, baseline=3)
