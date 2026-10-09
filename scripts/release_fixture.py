# SPDX-License-Identifier: AGPL-3.0-or-later
"""The upgrade fixture: the library a release makes, kept for the next release's upgrade test."""

from __future__ import annotations

import re
from pathlib import Path

from release_common import ROOT

#: WHERE THE UPGRADE TEST FINDS THE LAST RELEASE'S LIBRARY: one gzipped dump of a new library made by
#: the released code (`write_upgrade_fixture`), read by
#: `tests/integration/test_a_library_from_the_last_release_comes_forward.py`, newest by version.
FIXTURES = ROOT / "tests" / "integration" / "data"


FIXTURE = re.compile(r"^library-(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)\.sql\.gz$")


#: Run by the released interpreter: a new library, every component at its released version, made
#: through the same door a first boot makes one (`initialize_schema`), dumped whole to the path
#: it is handed. To a file and not to standard output, which the database's own log lines share.
#: Run from a file of its own (`write_upgrade_fixture`).
_MAKE_A_LIBRARY = """
import asyncio, sqlite3, sys, tempfile
from pathlib import Path

import sift.main  # every component registers its schema at import
from sift.kernel.db import Database


async def make(path):
    database = Database(path)
    await database.connect()
    try:
        await database.initialize_schema()
    finally:
        await database.close()


with tempfile.TemporaryDirectory() as folder:
    library = Path(folder) / "library.sqlite3"
    asyncio.run(make(library))
    connection = sqlite3.connect(library)
    try:
        dumped = "\\n".join(connection.iterdump()) + "\\n"
    finally:
        connection.close()
Path(sys.argv[1]).write_text(dumped, encoding="utf-8", newline="\\n")
"""


def without_sql_comments(sql: str) -> str:
    """SQL without its comments, each comment's line kept and nothing quoted touched."""
    out: list[str] = []
    at, end = 0, len(sql)
    quote: str | None = None
    while at < end:
        one = sql[at]
        if quote is not None:
            out.append(one)
            if one == quote:
                quote = None
            at += 1
            continue
        if one in "'\"`[":
            quote = "]" if one == "[" else one
            out.append(one)
            at += 1
            continue
        if sql.startswith("--", at):
            stop = sql.find("\n", at)
            at = end if stop == -1 else stop
            while out and out[-1] in " \t":
                out.pop()
            continue
        if sql.startswith("/*", at):
            stop = sql.find("*/", at + 2)
            at = end if stop == -1 else stop + 2
            continue
        out.append(one)
        at += 1
    return "".join(out)


def keep_one_fixture(folder: Path, version: str) -> list[Path]:
    """Delete every upgrade fixture in `folder` but this version's. Returns what went."""
    gone: list[Path] = []
    for one in sorted(folder.iterdir()) if folder.is_dir() else []:
        found = FIXTURE.match(one.name)
        if one.is_file() and found is not None and found["version"] != version:
            one.unlink()
            gone.append(one)
    return gone
