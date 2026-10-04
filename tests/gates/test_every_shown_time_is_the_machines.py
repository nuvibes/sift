# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every date and time Sift shows or groups by is the server machine's local time.

Storage is UTC (unix seconds, no zone beside them). A DAY and a TIME OF DAY depend on a zone, and
the zone is the machine's: one clock for the library, so a filing at 23:30 is that evening's line
on every screen and its date opens that evening's files. The server turns a moment into a day or a
time of day only through `kernel/when.py`, and SQL only through its `LOCAL_DAY_SQL` expression or
SQLite's `'localtime'`, which reads the same C runtime. The client writes every moment in the zone
the session names (`lib/shell/when.ts`, `clock.zone`), never the browser's.

This refuses the ways around that:

* on the server, outside the clock: a UTC day or time (`utcfromtimestamp`, `utcnow`, a moment made
  in UTC with `fromtimestamp(.., UTC)`, a wall clock read as UTC with `tzinfo=UTC`, `now(UTC)`),
  and the local readings that bypass the clock's zone refresh (`date.today()`, `datetime.now()`,
  `time.localtime`, `time.gmtime`, `time.strftime`, a bare `fromtimestamp`);
* in SQL: a day made by dividing a stored moment by a day's seconds without moving it onto the
  machine's clock first, and `'unixepoch'` without `'localtime'` after it;
* on the client, outside `lib/shell/when.ts`: a date's own local getters and formatters
  (`getDate()`, `getHours()`, `toLocaleDateString`, `new Intl.DateTimeFormat`), all of which read
  the browser's zone; and inside it, a formatter made without a `timeZone`.

An excuse names its file, how many of the pattern it holds, and why each is not a day or time a
person reads.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "sift"
CLIENT = ROOT / "frontend" / "src"

#: The one module a moment becomes a day or a time of day in.
CLOCK = "kernel/when.py"

#: A day or time made outside the machine's clock, one pattern per way.
SERVER_REFUSED = re.compile(
    r"utcfromtimestamp\(|utcnow\(|date\.today\(\)|datetime\.now\(\)|now\((?:tz=)?UTC\)"
    r"|time\.localtime\(|time\.gmtime\(|time\.strftime\(|fromtimestamp\(|tzinfo=UTC"
    r"|astimezone\(UTC\)|timezone\.utc"
)

#: A stored moment divided into days: seconds by 86400, milliseconds by 86400000.
DAY_DIVISION = re.compile(r"//? ?86_?400(?:_?000)?\b")
#: The only way a statement divides a moment into days: moved onto the machine's clock first.
LOCAL_DAY = re.compile(r"unixepoch\([^;]*?, 'unixepoch', 'localtime'\) / 86400\b")
#: A moment read in SQL as a date or time without the machine's zone.
UTC_IN_SQL = re.compile(r"'unixepoch'(?!, 'localtime')")

#: Where a refused pattern stands for something that is not a day or time a person reads, with how
#: many there are and why. A new one in the same file is still refused: the count has to move.
SERVER_EXCUSED: dict[str, tuple[int, str]] = {
    "kernel/naming.py": (
        1,
        "`datetime.now(UTC)` is an aware instant, turned onto the machine's zone by `_local_time`"
        " before a name is written with it",
    ),
    "slices/download/service_base.py": (
        1,
        "an aware instant handed to the name template, which writes it on the machine's clock"
        " (`kernel/naming.py`)",
    ),
    "slices/download/sources/argv.py": (1, "a posting moment as an aware instant, for the name"),
    "slices/download/sources/twitter.py": (
        1,
        "a post's moment read out of its number as an aware instant, for the name template, which"
        " writes it on the machine's clock",
    ),
    "slices/download/sources/discord.py": (1, "a posting moment as an aware instant, for the name"),
    "slices/download/sources/reddit.py": (1, "a posting moment as an aware instant, for the name"),
    "slices/download/sources/tiktok.py": (1, "a posting moment as an aware instant, for the name"),
    "slices/organize/batch.py": (
        1,
        "the moment a file arrived as an aware instant, handed to the name template",
    ),
    "slices/suggestions/naming_filenames.py": (
        1,
        "a stamp read out of a file name as a key that is never shown: it only has to be the same"
        " for two files a tool saved together",
    ),
}

#: A date's own readings on the client, every one of them in the browser's zone.
CLIENT_REFUSED = re.compile(
    r"\.(?:getDate|getDay|getHours|getMinutes|getSeconds|getMonth|getFullYear)\(\)"
    r"|\.(?:setHours|setDate|setMinutes)\(|toLocaleDateString\(|toLocaleTimeString\("
    r"|new Intl\.DateTimeFormat\("
)

#: Where the client may construct one anyway, and why.
CLIENT_EXCUSED: dict[str, tuple[int, str]] = {
    "lib/shell/when.ts": (
        0,
        "the one place a moment is written; its own formatters are checked below",
    ),
}


def _code_lines(path: Path) -> list[str]:
    """The file's lines, less the ones that are wholly a comment."""
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith(("#", "//", "*", "/*"))
    ]


def _server_files() -> list[Path]:
    return [
        path
        for path in SRC.rglob("*.py")
        if "tests" not in path.parts and "testing" not in path.parts
    ]


def _client_files() -> list[Path]:
    return [
        path
        for path in CLIENT.rglob("*")
        if path.suffix in {".ts", ".svelte"}
        and not path.name.endswith(".test.ts")
        and "design" not in path.relative_to(CLIENT).parts[:2]
    ]


def _server_uses() -> dict[str, int]:
    found: dict[str, int] = {}
    for path in _server_files():
        name = path.relative_to(SRC).as_posix()
        if name == CLOCK:
            continue
        count = sum(len(SERVER_REFUSED.findall(line)) for line in _code_lines(path))
        if count:
            found[name] = count
    return found


def test_no_day_or_time_is_made_outside_the_machines_clock() -> None:
    uses = _server_uses()
    strays = sorted(
        f"{name} ({count})"
        for name, count in uses.items()
        if SERVER_EXCUSED.get(name, (0, ""))[0] != count
    )
    assert not strays, (
        "these files turn a moment into a day or a time of day outside the machine's clock, which"
        " puts a filing at 23:30 on the next day for anybody west of Greenwich. Use"
        " `sift.kernel.when` (`day_of`, `day_number`, `wall`, `stamp`, `today`), or excuse it here"
        " with the reason it is not a day or time a person reads:\n  " + "\n  ".join(strays)
    )


def test_every_day_in_sql_is_the_machines_day() -> None:
    wrong: list[str] = []
    for path in _server_files():
        name = path.relative_to(SRC).as_posix()
        for line in _code_lines(path):
            divided = len(DAY_DIVISION.findall(line))
            if (divided and divided != len(LOCAL_DAY.findall(line))) or UTC_IN_SQL.search(line):
                wrong.append(f"{name}: {line.strip()}")
    assert not wrong, (
        "a day made by dividing a stored moment without moving it onto the machine's clock is a"
        " UTC day. Spell it `unixepoch(<column>, 'unixepoch', 'localtime') / 86400`"
        " (`kernel/when.py` `LOCAL_DAY_SQL`):\n  " + "\n  ".join(wrong)
    )


def test_the_client_writes_no_moment_in_the_browsers_zone() -> None:
    found: dict[str, int] = {}
    for path in _client_files():
        name = path.relative_to(CLIENT).as_posix()
        count = sum(len(CLIENT_REFUSED.findall(line)) for line in _code_lines(path))
        if count:
            found[name] = count
    strays = sorted(
        f"{name} ({count})"
        for name, count in found.items()
        if name != "lib/shell/when.ts" and CLIENT_EXCUSED.get(name, (0, ""))[0] != count
    )
    assert not strays, (
        "these read a date in the browser's zone, which is not the zone the library keeps its"
        " days in. Write the moment through `lib/shell/when.ts`:\n  " + "\n  ".join(strays)
    )


def test_every_formatter_in_the_clock_names_its_zone() -> None:
    """Each `Intl.DateTimeFormat` in `lib/shell/when.ts` says which zone it writes in: the server's (the
    `timeZone` the session named) or UTC for a day or a setting's time that has none."""
    text = (CLIENT / "lib" / "shell" / "when.ts").read_text(encoding="utf-8")
    made = [match.start() for match in re.finditer(r"new Intl\.DateTimeFormat\(", text)]
    assert made, "lib/shell/when.ts makes no formatter: the check below would be watching nothing"
    unnamed = [
        text[start : text.index("})", start) + 2]
        for start in made
        if "timeZone" not in text[start : text.index("})", start)]
    ]
    assert not unnamed, "a formatter with no timeZone writes in the browser's zone:\n" + "\n".join(
        unnamed
    )


def test_every_excuse_still_stands() -> None:
    """An excuse for a file that no longer holds the pattern is a gate watching nothing."""
    uses = _server_uses()
    gone = sorted(name for name, (count, _) in SERVER_EXCUSED.items() if uses.get(name) != count)
    assert not gone, f"excused but no longer matching, so update or take them off: {gone}"
    for name, (count, _) in CLIENT_EXCUSED.items():
        text = "\n".join(_code_lines(CLIENT / name))
        if count:
            assert len(CLIENT_REFUSED.findall(text)) == count, name
