# SPDX-License-Identifier: AGPL-3.0-or-later
"""The end of the log, and a copy of it fit to leave the machine.

Admin-only, and that is the control rather than a courtesy. A log line carries whatever the line
that wrote it passed (a library path, a file name, a person's name) and while every record goes
through the scrubber before it is written, the honest answer to a guest asking what this
installation has been doing is still no.

**Read-only, and there is deliberately no way to clear it from here.** What the log may take on disk
is already a setting, and Sift deletes the oldest as it fills; a button that threw the record away
would be a button whose only use is on the day somebody most wants to read it.

The tail rather than the file: see `tail_of` for why a whole-file read is not an option on a log
whose ceiling is a gigabyte.
"""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response

from sift.kernel.access import Viewer
from sift.kernel.log import LOG_FILENAME
from sift.kernel.wiring import SETTINGS, part_of
from sift.logbundle import write_archive
from sift.slices.auth import require_admin
from sift.slices.logs.models import LogLine, LogPage
from sift.slices.logs.tail import MOST_LINES, SEARCH_BUDGET, newest_matching

router = APIRouter(tags=["logs"])

#: How many lines a screen gets when it does not say. A screenful and some scrollback.
DEFAULT_LINES = 200

#: The levels a record can carry, quietest first, as structlog writes them. A filter names ONE and
#: keeps it and everything louder ("at least this bad"): nobody means errors without critical ones.
LEVELS: tuple[str, ...] = ("debug", "info", "warning", "error", "critical")

Level = Literal["debug", "info", "warning", "error", "critical"]

#: How many rotated files beside the log are looked through, at most. Far past any `log_backups` a
#: person would set, and a bound, so a directory somebody filled with `sift.log.N` by hand is not a
#: walk without an end.
_MOST_ROTATIONS = 50

#: The two keys every record carries that a search is never about. "info" matching every line
#: because every line says `"level": "info"` is a search box that cannot filter anything.
_NOT_SEARCHED = frozenset({"timestamp", "level"})

#: The longest search text taken. A search box, not a place to paste a log into.
_MOST_SEARCH = 200


def _parsed(line: str) -> LogLine:
    """One line, taken apart if it will come apart.

    A line that is not JSON, or is JSON that is not an object, keeps its raw text and nothing else.
    That is not a failure worth reporting: a log holds whatever was written to it, including output
    from a library that knows nothing about Sift's format, and a page that refused to draw because
    one line was odd would be useless exactly when it is wanted.
    """
    try:
        held: Any = json.loads(line)
    except ValueError:
        return LogLine(raw=line)
    if not isinstance(held, dict):
        return LogLine(raw=line)
    return LogLine(
        at=_text(held.get("timestamp")),
        level=_text(held.get("level")),
        event=_text(held.get("event")),
        raw=line,
    )


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


@router.get("/logs", response_model=LogPage)
async def recent(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
    lines: Annotated[int, Query(ge=1, le=MOST_LINES)] = DEFAULT_LINES,
    level: Annotated[Level | None, Query()] = None,
    search: Annotated[str | None, Query(max_length=_MOST_SEARCH)] = None,
) -> LogPage:
    """The end of the log, oldest first. Narrowed, where asked, to a level and a search.

    Oldest first because that is the order it was written in and the order anything quoting it will
    be read in. A screen that wants the newest at the top can turn it over; a reader following a
    sequence of events cannot put one back together.

    ## Narrowed HERE, and not on the screen

    A screen that filtered what it was sent would be filtering the last two hundred lines, so
    "errors only" on a busy log would show the errors among the last two hundred lines, which is
    usually none, while the error somebody is looking for sits a thousand lines up. Narrowing where
    the file is read means the two hundred lines sent are two hundred ERRORS, and the rest of a
    large log never crosses the wire. `level` keeps that level and everything louder; `search` keeps
    a line whose event or fields contain the words, ignoring case.

    Off the event loop, like every other file read in Sift: the log lives beside the database, which
    on a self-hosted install may be a network mount, and a stat on one of those is not instant.
    """
    settings = part_of(request, SETTINGS)
    path = Path(settings.data_dir) / LOG_FILENAME
    found = await asyncio.to_thread(_read, path, lines, level, search)
    return found


def _read(path: Path, lines: int, level: str | None = None, search: str | None = None) -> LogPage:
    """The blocking half, so the route above is one `to_thread` and nothing else."""
    try:
        size = path.stat().st_size
    except OSError:
        return LogPage(lines=[], path=str(path), present=False)
    keep = _keeps(level, search)
    tail = newest_matching(
        _with_rotations(path),
        lines,
        keep,
        # Bounded only when filtered. An unfiltered read keeps every line, so it has what it asked
        # for within a few blocks and a budget would change nothing.
        budget=None if keep is None else SEARCH_BUDGET,
    )
    return LogPage(
        lines=[_parsed(one) for one in tail.lines],
        path=str(path),
        size_bytes=size,
        present=True,
        searched_bytes=tail.read_bytes,
        whole=tail.whole,
    )


def _with_rotations(path: Path) -> list[Path]:
    """The log and the older files rotation keeps beside it, newest first. See `newest_matching`.

    Read as well as the current file because a rotation can happen a second before somebody looks:
    the current file is then nearly empty and the error they came for is in `.1`.
    """
    return [path, *(path.with_name(f"{path.name}.{n}") for n in range(1, _MOST_ROTATIONS + 1))]


def _keeps(level: str | None, search: str | None) -> Callable[[str], bool] | None:
    """What a filtered read keeps, as one test of a raw line. None when nothing filters it.

    A line that is not one of Sift's records has no level, so it is kept only when every level is
    wanted (`debug`, Debug on the screen): a level filter is a promise that what is shown is at
    least that bad, and a line nobody can rate cannot be shown under that promise. A search still
    reads its text, since the text is all it has.
    """
    floor = LEVELS.index(level) if level in LEVELS else 0
    needle = (search or "").strip().casefold()
    if floor == 0 and not needle:
        return None

    def keep(raw: str) -> bool:
        record = _record(raw)
        if floor > 0:
            said = record.get("level") if record is not None else None
            rank = LEVELS.index(said) if isinstance(said, str) and said in LEVELS else -1
            if rank < floor:
                return False
        if not needle:
            return True
        return needle in (raw if record is None else _searched_text(record)).casefold()

    return keep


def _record(raw: str) -> dict[str, Any] | None:
    try:
        held: Any = json.loads(raw)
    except ValueError:
        return None
    return held if isinstance(held, dict) else None


def _searched_text(record: dict[str, Any]) -> str:
    """The event, then every other field but the two every line carries as `key=value`, the same
    words the screen draws for a line, so what matches is what somebody can see matched."""
    event = record.get("event")
    fields = (
        f"{key}={value if isinstance(value, str) else json.dumps(value)}"
        for key, value in record.items()
        if key not in _NOT_SEARCHED and key != "event"
    )
    return "  ".join([event if isinstance(event, str) else "", *fields])


@router.get(
    "/logs/archive",
    response_class=Response,
    responses={200: {"content": {"application/zip": {}}}},
)
async def archive(
    request: Request,
    _admin: Annotated[Viewer, Depends(require_admin)],
) -> Response:
    """`Download log`: the library's log whole and unfiltered, redacted whatever the setting says,
    as the zip the desktop app makes of both its places (`sift.logbundle`)."""
    folder = Path(part_of(request, SETTINGS).data_dir)
    made = io.BytesIO()
    await asyncio.to_thread(write_archive, made, [("library", folder)])
    return Response(made.getvalue(), media_type="application/zip")
