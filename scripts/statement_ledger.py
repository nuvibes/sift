# SPDX-License-Identifier: AGPL-3.0-or-later
"""Write the statement ledger: every screen's and press's statements, priced in SQLite's steps.

    python scripts/statement_ledger.py --record
    python scripts/statement_ledger.py --raise select:jobs#4f03d689 --why "..." [--per person]
    python scripts/statement_ledger.py --raise NAME --boot --why "..."  # may run before ready
    python scripts/statement_ledger.py --start          # a SQLite version with no record yet

Walks the synthetic library at both sizes (`sift.testing.statement_ledger`) and brings the record
for this SQLite version to what it saw. A fall is written; a statement no longer run leaves. A
rise, a new statement or a new walk of a library-sized table is written only for a name given
with `--raise` and a reason, which is kept beside the number, so the change and its sentence are
reviewed together. Nothing is written when anything is refused.
"""

from __future__ import annotations

import argparse
import sys
import time

from sift.testing import statement_ledger as ledger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--record", action="store_true", help="write falls and removals")
    parser.add_argument("--raise", dest="raised", action="append", default=[], metavar="NAME")
    parser.add_argument("--why", default=None, help="why the named statements may cost more")
    parser.add_argument("--per", default=None, choices=sorted(ledger.SUBJECTS))
    parser.add_argument(
        "--boot", action="store_true", help="the reason is why it may run before boot.ready"
    )
    parser.add_argument("--start", action="store_true", help="the first record for this SQLite")
    args = parser.parse_args()
    if args.raised and not args.why:
        parser.error("--raise needs --why: the reason is kept beside the number")
    if not (args.record or args.raised or args.start):
        parser.error("say --record, --raise NAME --why ..., or --start")

    record = ledger.read_record()
    version = ledger.version()
    if args.start == (version in record):
        print(
            f"SQLite {version}: "
            + ("already recorded; use --record" if args.start else "no record yet; use --start")
        )
        return 1

    started = time.perf_counter()
    walks = {size: ledger.walk(size).seen for size in ledger.SIZES}
    if args.start:
        block = ledger.first_block(walks)
        refused: list[str] = []
    else:
        raised = {name: ledger.Raise(args.why, args.per, args.boot) for name in args.raised}
        block, refused = ledger.updated(record[version], walks, raised)
    for line in refused:
        print(f"refused: {line}")
    if refused:
        return 1
    record[version] = block
    ledger.write_record(record)
    print(
        f"SQLite {version}: {len(block)} statements recorded"
        f" in {time.perf_counter() - started:.0f}s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
